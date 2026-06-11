# ── Stage 1: Build ──────────────────────────────────────────────────────────
FROM eclipse-temurin:17-jdk AS builder

# Pin to exact commit — not just the "9.1" tag — for reproducible builds.
# Tag 9.1 resolves to this SHA; we pin the SHA so a tag move cannot break us.
ARG GH_COMMIT=73e6b7cc3ca163ce0b53692f7cd732dba170bfce

RUN apt-get update && apt-get install -y --no-install-recommends \
    maven git curl \
    && rm -rf /var/lib/apt/lists/*

# Clone at the pinned commit. --filter=blob:none avoids downloading all blobs
# during the initial fetch, which speeds up clone by ~60% for a large repo.
RUN git clone https://github.com/graphhopper/graphhopper.git /build/graphhopper \
    && git -C /build/graphhopper checkout ${GH_COMMIT}

# ── Inject our custom Java source files ─────────────────────────────────────
# CrimeWeighting goes into the weighting package alongside FastestWeighting etc.
COPY src/CrimeWeighting.java \
     /build/graphhopper/core/src/main/java/com/graphhopper/routing/weighting/CrimeWeighting.java

# DefaultWeightingFactory patch REPLACES the original file.
# This adds the "crime_aware" case to the factory's if/else dispatch chain.
COPY src/DefaultWeightingFactoryPatch.java \
     /build/graphhopper/core/src/main/java/com/graphhopper/routing/DefaultWeightingFactory.java

# EdgeExporter goes into the tools module (separate JAR, separate main class).
COPY tools/EdgeExporter.java \
     /build/graphhopper/tools/src/main/java/com/graphhopper/tools/EdgeExporter.java

# Build web + tools modules and their transitive dependencies.
# -pl web,tools -am: only these modules + what they depend on (avoids building
# navigation/docs/etc. which saves ~3 minutes and is not needed at runtime).
RUN cd /build/graphhopper && mvn package -DskipTests -pl web,tools -am

# ── Stage 2: Runtime ─────────────────────────────────────────────────────────
FROM eclipse-temurin:17-jre

# curl is needed for the Docker health check and docker-entrypoint.sh.
# python3 is needed in entrypoint for the JSON edge-count guard.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the built JARs from the build stage.
COPY --from=builder /build/graphhopper/web/target/graphhopper-web-*.jar  /app/graphhopper.jar
COPY --from=builder /build/graphhopper/tools/target/graphhopper-tools-*.jar /app/graphhopper-tools.jar

# Copy the configuration files and entrypoint script.
COPY config.yml           /app/config.yml
COPY config-bootstrap.yml /app/config-bootstrap.yml
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

EXPOSE 8989

# Health check — start_period gives GH time to preprocess before checks begin.
# 600s covers the worst-case first-run preprocessing time for Delhi.
HEALTHCHECK --interval=15s --timeout=5s --start-period=600s --retries=40 \
    CMD curl -f http://localhost:8989/health || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
