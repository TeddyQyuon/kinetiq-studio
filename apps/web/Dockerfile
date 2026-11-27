FROM node:22-alpine AS builder

WORKDIR /repo
COPY package.json ./
COPY packages/contracts ./packages/contracts
COPY packages/remotion-studio ./packages/remotion-studio
COPY apps/web ./apps/web

ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

RUN npm install --workspace @kinetiq/web --include-workspace-root
RUN npm --workspace @kinetiq/web run build

FROM node:22-alpine AS runner
WORKDIR /repo
ENV NODE_ENV=production
COPY --from=builder /repo ./
EXPOSE 3000
CMD ["npm", "--workspace", "@kinetiq/web", "run", "start"]
