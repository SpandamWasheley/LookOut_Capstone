const fs = require('fs');
const path = require('path');
const root = path.join(__dirname, 'src');
const files = [];
function walk(dir) {
  for (const name of fs.readdirSync(dir)) {
    const p = path.join(dir, name);
    if (fs.statSync(p).isDirectory()) walk(p);
    else if (p.endsWith('.jsx')) files.push(p);
  }
}
walk(root);
for (const file of files) {
  let text = fs.readFileSync(file, 'utf8');
  let changed = false;
  const original = text;
  text = text.replace(/interface\s+\w+\s*\{[\s\S]*?\}\s*/g, '');
  text = text.replace(/type\s+\w+\s*=\s*[\s\S]*?;/g, '');
  text = text.replace(/as\s+const/g, '');
  text = text.replace(/as\s+[A-Za-z0-9_\[\]\.<>]+/g, '');
  text = text.replace(/:\s*React\.FormEvent/g, '');
  text = text.replace(/:\s*React\.ElementType/g, '');
  text = text.replace(/:\s*React\.ReactNode/g, '');
  text = text.replace(/:\s*React\.Node/g, '');
  text = text.replace(/:\s*React\.MouseEvent[^\)\n]*/g, '');
  text = text.replace(/:\s*\w+\[\]\s*(?=[=,\);\n])/g, '');
  text = text.replace(/:\s*\(.*?\)\s*=>/g, '');
  text = text.replace(/:\s*\{[^}]*\}/g, '');
  text = text.replace(/:\s*\[.*?\]/g, '');
  text = text.replace(/:\s*[^=,\)\n\]+]+(?=[,\)\n;])/g, '');
  text = text.replace(/useState<[^>]+>/g, 'useState');
  text = text.replace(/useRef<[^>]+>/g, 'useRef');
  text = text.replace(/useMemo<[^>]+>/g, 'useMemo');
  text = text.replace(/useCallback<[^>]+>/g, 'useCallback');
  text = text.replace(/\bRecord<[^>]+>\b/g, 'Object');
  text = text.replace(/const\s+(\w+)\s*:\s*[^=]+=/g, 'const $1 =');
  text = text.replace(/let\s+(\w+)\s*:\s*[^=;]+/g, 'let $1');
  text = text.replace(/var\s+(\w+)\s*:\s*[^=;]+/g, 'var $1');
  text = text.replace(/\w+\s*:\s*\w+\s*\|\s*\w+/g, (m) => m.replace(/:\s*\w+\s*\|\s*\w+/, ''));
  text = text.replace(/\bReact\.ElementType\b/g, 'React.ComponentType');
  text = text.replace(/\bReact\.ReactNode\b/g, 'React.ReactNode');
  text = text.replace(/\bAlertStatus\b/g, '');
  text = text.replace(/\bOfficer\b/g, '');
  text = text.replace(/\bAlert\b/g, '');
  text = text.replace(/\bResident\b/g, '');
  text = text.replace(/\bHousehold\b/g, '');
  text = text.replace(/\bHouseholdMember\b/g, '');
  text = text.replace(/\bOfficerAssignment\b/g, '');
  text = text.replace(/\bOfficerRecord\b/g, '');
  text = text.replace(/\bEnrollForm\b/g, '');
  text = text.replace(/\bEnrollStep\b/g, '');
  if (text !== original) {
    fs.writeFileSync(file, text, 'utf8');
    changed = true;
  }
  if (changed) console.log('fixed', path.relative(__dirname, file));
}
console.log('Done', files.length, 'files processed');
