// esbuild bundles `import './x.css'` into a sibling stylesheet; tsc only needs
// the module to type-resolve (the import has no runtime value we use).
declare module '*.css';
