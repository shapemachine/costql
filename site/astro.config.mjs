// @ts-check
import react from '@astrojs/react';
import starlight from '@astrojs/starlight';
import { defineConfig } from 'astro/config';
import rehypeExternalLinks from 'rehype-external-links';

// https://astro.build/config
export default defineConfig({
  site: 'https://costql.com',
  markdown: {
    // links that leave costql.com open in a new tab
    rehypePlugins: [[rehypeExternalLinks, { target: '_blank', rel: ['noopener'] }]],
  },
  integrations: [
    starlight({
      title: 'costQL',
      description:
        'Price GraphQL queries before you run them. Build a static pricing pack once, quote any query offline in Python or JavaScript.',
      social: [
        { icon: 'github', label: 'GitHub', href: 'https://github.com/shapemachine/costql' },
      ],
      customCss: ['./src/styles/theme.css'],
      // light-only: pin the theme and drop the light/dark picker.
      // Head is overridden to inject Vercel Web Analytics on the docs pages.
      components: {
        ThemeProvider: './src/components/starlight/ThemeProvider.astro',
        ThemeSelect: './src/components/starlight/ThemeSelect.astro',
        Head: './src/components/starlight/Head.astro',
      },
      sidebar: [
        { label: 'Playground', link: '/playground/' },
        { label: 'Quickstart', slug: 'docs/quickstart' },
        { label: 'Tier fidelity', slug: 'docs/tiers' },
        { label: 'Writing an adapter', slug: 'docs/adapters' },
        { label: 'Agent-assisted onboarding', slug: 'docs/agents' },
        { label: 'Instrumenting for T2/T3', slug: 'docs/instrumentation' },
        { label: 'External calls', slug: 'docs/external-calls' },
        { label: 'FAQ', slug: 'docs/faq' },
        {
          label: 'Reference',
          items: [
            { label: 'The output contract', slug: 'docs/contract' },
            { label: 'Price Pack Format', slug: 'docs/pack-format' },
            { label: 'Architecture', slug: 'docs/architecture' },
            { label: 'The JS package', slug: 'docs/js' },
            { label: 'Limitations', slug: 'docs/limitations' },
          ],
        },
        {
          label: 'Evidence',
          items: [
            { label: 'Evaluation methodology', slug: 'docs/evaluation' },
            { label: 'The demo APIs', slug: 'docs/demo-apis' },
            { label: 'Case study: TMDB', slug: 'docs/results/tmdb' },
            { label: 'Case study: Rick & Morty', slug: 'docs/results/rickmorty' },
            { label: 'Case study: Northwind', slug: 'docs/results/northwind' },
          ],
        },
      ],
    }),
    react(),
  ],
});
