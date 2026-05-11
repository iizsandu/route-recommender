# Stage 1: build
FROM node:20-alpine AS build
WORKDIR /app

# WHY: copy lockfile first — npm ci layer is cached unless deps change
COPY package*.json ./
RUN npm ci

COPY . .

# WHY: VITE_MAPTILER_KEY is a public key; baking it into the bundle is safe
# because MapTiler enforces domain restrictions on their end
ARG VITE_MAPTILER_KEY
ENV VITE_MAPTILER_KEY=$VITE_MAPTILER_KEY

RUN npm run build

# Stage 2: serve
FROM nginx:alpine

# WHY: only dist/ crosses the stage boundary — no source, no node_modules,
# no compiler in the final image. Final image is ~25 MB vs ~400 MB.
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
