# syntax=docker/dockerfile:1
# 前端镜像：Next.js 静态导出(output:"export") → 产物 out/ 交给 nginx 托管
# 构建上下文 = Tutor-Chatbot-main/
FROM node:20-alpine AS build
WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./

# 关键：静态导出在「构建期」把 NEXT_PUBLIC_* 写死进产物。
# /api 由 nginx 反代剥前缀到后端，浏览器同源，避免远程用户连到自己的 127.0.0.1。
ARG NEXT_PUBLIC_BACKEND_URL=/api
ENV NEXT_PUBLIC_BACKEND_URL=$NEXT_PUBLIC_BACKEND_URL
RUN npm run build

FROM nginx:1.27-alpine
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/out /usr/share/nginx/html
EXPOSE 80
