# 前端镜像：Next.js 生产构建
FROM node:22-slim

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# 构建时注入后端 API 地址（Next.js public env 为构建期变量）
ARG NEXT_PUBLIC_API_URL=http://124.223.217.62:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

RUN npm run build

EXPOSE 3000
CMD ["npm", "start"]
