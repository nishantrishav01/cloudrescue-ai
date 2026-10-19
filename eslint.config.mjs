import eslint from '@eslint/js';
import typescript from 'typescript-eslint';
import awscdk from 'eslint-plugin-awscdk';

export default typescript.config(
  { ignores: ['**/dist/**', '**/node_modules/**', '**/cdk.out/**'] },
  eslint.configs.recommended,
  ...typescript.configs.recommended,
  { files: ['infrastructure/**/*.ts'], plugins: { awscdk }, rules: { '@typescript-eslint/no-explicit-any': 'error' } },
);
