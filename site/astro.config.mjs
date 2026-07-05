// @ts-check
import react from '@astrojs/react';
import starlight from '@astrojs/starlight';
import { defineConfig } from 'astro/config';

// https://astro.build/config
export default defineConfig({
  site: 'https://costql.com',
  integrations: [
    starlight({
      title: 'costQL',
      description:
        'Price GraphQL queries before you run them — build a static pricing pack once, quote any query offline in Python or JavaScript.',
      social: [
        { icon: 'github', label: 'GitHub', href: 'https://github.com/shapemachine/costql' },
      ],
      customCss: ['./src/styles/theme.css'],
      sidebar: [
        { label: 'Quickstart', slug: 'docs/quickstart' },
        { label: 'Tier fidelity', slug: 'docs/tiers' },
        { label: 'Writing an adapter', slug: 'docs/adapters' },
        { label: 'Instrumenting for T2/T3', slug: 'docs/instrumentation' },
        {
          label: 'Reference',
          items: [
            { label: 'The output contract', slug: 'docs/contract' },
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
