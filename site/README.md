# site

The marketing page at [riffle.dev](https://riffle.dev). Next.js, no backend,
no product code — the dashboard lives in [`services/app`](../services/app).

```bash
bun install
bun dev
bun run brand   # regenerate brand icons from source SVGs
```

Deployed on Vercel with **Root Directory = `site`**.

> This Next.js version has breaking changes against upstream defaults. Read
> `node_modules/next/dist/docs/` before writing code here.
