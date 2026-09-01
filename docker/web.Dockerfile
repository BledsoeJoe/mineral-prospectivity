# Build the React app, then serve it from nginx alongside an /api proxy.
FROM node:22-alpine AS build
WORKDIR /app
COPY web/package.json web/package-lock.json* ./
RUN npm install
COPY web ./
ARG VITE_CARTO_KEY
RUN npm run build

FROM nginx:1.27-alpine
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
