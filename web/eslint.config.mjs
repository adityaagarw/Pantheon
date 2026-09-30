import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  globalIgnores([".next/**", "out/**", "build/**", "next-env.d.ts"]),
  {
    rules: {
      // "Reset then fetch" effects are intentional throughout the app.
      "react-hooks/set-state-in-effect": "off",
    },
  },
  {
    // The 3D office mutates three.js objects and motion state every frame
    // (useFrame) — the idiomatic react-three-fiber pattern, which these
    // React-Compiler purity rules cannot model.
    files: ["src/office/**"],
    rules: {
      "react-hooks/immutability": "off",
      "react-hooks/refs": "off",
    },
  },
]);

export default eslintConfig;
