from __future__ import annotations

import logging
from typing import Optional

import torch
from transformers import BartForConditionalGeneration, BartTokenizer
from tqdm import tqdm

logger = logging.getLogger(__name__)

_MODEL_NAME = "sshleifer/distilbart-cnn-6-6"
_MAX_INPUT_TOKENS = 1024
_MAX_SUMMARY_TOKENS = 120
_MIN_SUMMARY_TOKENS = 30
# WHY: texts shorter than this are already concise — don't waste model compute on them
_SHORT_TEXT_THRESHOLD = 50

_tokenizer: Optional[BartTokenizer] = None
_model: Optional[BartForConditionalGeneration] = None


def _load_model() -> tuple[BartTokenizer, BartForConditionalGeneration]:
    global _tokenizer, _model
    if _tokenizer is None:
        # WHY: lazy-load so importing this module doesn't download 600MB on every script run
        device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info("Loading distilbart model on %s (one-time cost)...", device)
        _tokenizer = BartTokenizer.from_pretrained(_MODEL_NAME)
        _model = BartForConditionalGeneration.from_pretrained(_MODEL_NAME).to(device)
        _model.eval()
        logger.info("distilbart ready")
    return _tokenizer, _model


def summarize_batch(texts: list[str], batch_size: int | None = None) -> list[str]:
    """
    Summarize a list of article texts using distilbart-cnn-6-6.

    Returns a list of the same length and order as the input.
    Texts shorter than 50 chars are returned as-is.
    Input is truncated at 1024 tokens before passing to the model.
    """
    if not texts:
        return []

    tokenizer, model = _load_model()
    device = next(model.parameters()).device

    if batch_size is None:
        # WHY: GPU can hold 16 sequences in VRAM; CPU keeps 4 to avoid OOM
        batch_size = 16 if device.type == "cuda" else 4

    summaries: list[str] = []
    batches = range(0, len(texts), batch_size)
    for i in tqdm(batches, desc=f"Summarizing ({device})", unit="batch"):
        chunk = texts[i : i + batch_size]
        summaries.extend(_process_chunk(chunk, tokenizer, model, device))

    return summaries


def _process_chunk(
    texts: list[str],
    tokenizer: BartTokenizer,
    model: BartForConditionalGeneration,
    device: torch.device,
) -> list[str]:
    results: list[str] = [""] * len(texts)

    # Split into pass-through (short) and texts that need the model
    model_indices: list[int] = []
    model_texts: list[str] = []
    for idx, text in enumerate(texts):
        stripped = text.strip()
        if len(stripped) < _SHORT_TEXT_THRESHOLD:
            results[idx] = stripped
        else:
            model_indices.append(idx)
            model_texts.append(stripped)

    if not model_texts:
        return results

    try:
        # WHY: use BartTokenizer directly — pipeline("summarization") has a known
        # padding bug in transformers >= 4.36 that corrupts multi-text batch outputs
        inputs = tokenizer(
            model_texts,
            max_length=_MAX_INPUT_TOKENS,
            truncation=True,
            padding=True,
            return_tensors="pt",
        ).to(device)

        with torch.no_grad():
            # WHY: no_grad() disables gradient tracking — halves memory use during inference
            output_ids = model.generate(
                inputs["input_ids"],
                attention_mask=inputs["attention_mask"],
                max_new_tokens=_MAX_SUMMARY_TOKENS,
                min_length=_MIN_SUMMARY_TOKENS,
                num_beams=4,
                early_stopping=True,
            )

        decoded = tokenizer.batch_decode(output_ids, skip_special_tokens=True)
        for result_idx, summary in zip(model_indices, decoded):
            results[result_idx] = summary

    except Exception:
        # WHY: don't crash the entire 12K-record pipeline over one bad batch;
        # fall back to the first 500 chars of the original article
        logger.exception(
            "Summarization failed for batch of %d texts; using truncated originals",
            len(model_texts),
        )
        for result_idx, text in zip(model_indices, model_texts):
            results[result_idx] = text[:500]

    return results
