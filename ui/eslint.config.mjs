import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import { defineConfig, globalIgnores } from "eslint/config";
import tseslint from "typescript-eslint";

export default defineConfig([
  globalIgnores(["dist/**", "public/**"]),
  js.configs.recommended,
  ...tseslint.configs.recommended,
  reactHooks.configs.flat["recommended-latest"],
  {
    rules: {
      // "Reset then fetch" effects are intentional throughout the app.
      "react-hooks/set-state-in-effect": "off",
    },
  },
  {
    // The 3D office mutates three.js objects and motion state every frame
    // (useFrame), the idiomatic react-three-fiber pattern, which these
    // React-Compiler purity rules cannot model.
    files: ["src/office/**"],
    rules: {
      "react-hooks/immutability": "off",
      "react-hooks/refs": "off",
    },
  },
]);
