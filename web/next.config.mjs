const nextConfig = {
  distDir: process.env.VERMAY_NEXT_DIST_DIR ?? ".next",
  output: "standalone",
  reactStrictMode: true,
  turbopack: {
    root: process.cwd()
  },
  async redirects() {
    return [
      {
        source: "/",
        destination: "/agent",
        permanent: false
      }
    ]
  }
}

export default nextConfig
