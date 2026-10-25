// Checks formulas with KaTeX. Called by katex_validator.py.
// Input on stdin: a JSON list of {"expression": "...", "display": true or false}.
// Output on stdout: a JSON list with null for a valid formula and the error message otherwise.
const katex = require('katex');

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => { input += chunk; });
process.stdin.on('end', () => {
  const formulas = JSON.parse(input);
  const results = formulas.map(({ expression, display }) => {
    try {
      katex.renderToString(expression, { throwOnError: true, displayMode: display });
      return null;
    } catch (error) {
      return String(error.message).split('\n')[0];
    }
  });
  process.stdout.write(JSON.stringify(results));
});
