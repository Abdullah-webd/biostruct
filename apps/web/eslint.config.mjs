// eslint-config-next 16 ships flat configs directly, so no eslintrc compat
// layer is needed (and the compat layer crashes on its plugin graph).
import coreWebVitals from "eslint-config-next/core-web-vitals";
import typescript from "eslint-config-next/typescript";

const config = [
  { ignores: [".next/**", "node_modules/**", "scripts/**", "next-env.d.ts"] },
  ...(Array.isArray(coreWebVitals) ? coreWebVitals : [coreWebVitals]),
  ...(Array.isArray(typescript) ? typescript : [typescript]),
  {
    rules: {
      // This warning is about the pages router's _document, which the App
      // Router does not have. Fonts are preconnected and loaded in the root
      // layout, which applies to every route.
      "@next/next/no-page-custom-font": "off",
    },
  },
];

export default config;
