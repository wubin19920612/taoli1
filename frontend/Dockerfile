FROM node:22-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN NODE_OPTIONS=--max-old-space-size=768 npm run build
# Compress immutable build output once in CI, keeping originals for clients
# that do not accept gzip. The production server only serves these files.
RUN find dist -type f -size +1024c \( -name '*.js' -o -name '*.css' \) -exec gzip -9 -k {} \;

FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
