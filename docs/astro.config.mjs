// @ts-check
import starlight from "@astrojs/starlight";
import { defineConfig } from "astro/config";

export default defineConfig({
  site: "https://jina-code-systems.github.io",
  base: "/jina-connect-unified-cpaas",
  integrations: [
    starlight({
      title: "Jina Connect",
      logo: {
        light: "./src/assets/logo-light.svg",
        dark: "./src/assets/logo-dark.svg",
        replacesTitle: true,
      },
      description:
        "Open-source unified CPaaS — WhatsApp, Telegram, SMS, RCS, Voice. Multi-provider. AI-native. Self-hostable.",
      social: [
        {
          icon: "github",
          label: "GitHub",
          href: "https://github.com/JINA-CODE-SYSTEMS/jina-connect-unified-cpaas",
        },
        {
          icon: "discord",
          label: "Discord",
          href: "https://discord.gg/jbN5cwKR",
        },
        {
          icon: "x.com",
          label: "X",
          href: "https://x.com/tryjinaconnect",
        },
      ],
      customCss: ["./src/styles/custom.css"],
      editLink: {
        baseUrl:
          "https://github.com/JINA-CODE-SYSTEMS/jina-connect-unified-cpaas/edit/main/docs/",
      },
      sidebar: [
        {
          label: "Getting Started",
          autogenerate: { directory: "getting-started" },
        },
        {
          label: "Architecture",
          autogenerate: { directory: "architecture" },
        },
        {
          label: "Channels",
          autogenerate: { directory: "channels" },
        },
        {
          label: "API Reference",
          autogenerate: { directory: "api" },
        },
        {
          label: "MCP Server",
          autogenerate: { directory: "mcp" },
        },
        {
          label: "Deployment",
          autogenerate: { directory: "deployment" },
        },
      ],
    }),
  ],
});
