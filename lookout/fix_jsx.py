import os
import re
root = os.path.join(os.getcwd(), 'src')
changed_files = []
for dirpath, _, filenames in os.walk(root):
    for name in filenames:
        if not name.endswith('.jsx'):
            continue
        path = os.path.join(dirpath, name)
        with open(path, 'r', encoding='utf-8') as f:
            text = f.read()
        original = text
        text = re.sub(r'export\s+interface\s+\w+\s*\{[^}]*\}', '', text, flags=re.S)
        text = re.sub(r'interface\s+\w+\s*\{[^}]*\}', '', text, flags=re.S)
        text = re.sub(r'type\s+\w+\s*=\s*[^;]+;', '', text)
        text = re.sub(r'\bas\s+const\b', '', text)
        text = re.sub(r'\bas\s+[A-Za-z0-9_\[\]\.<>]+\b', '', text)
        text = re.sub(r'\bRecord<[^>]+>\b', '', text)
        text = re.sub(r'\bReact\.ElementType\b', 'React.ComponentType', text)
        text = re.sub(r'\bReact\.ReactNode\b', 'React.ReactNode', text)
        # Remove generic type args in hooks
        text = re.sub(r'use(State|Ref|Memo|Callback)<[^>]+>', r'use\1', text)
        # Remove explicit variable type annotations
        text = re.sub(r'\b(const|let|var)\s+(\w+)\s*:\s*[^=;]+(?==|;|$)', r'\1 \2', text)
        # Remove explicit type annotation from const declarations
        text = re.sub(r'\b(const|let|var)\s+([^:=\n]+?):\s*[^=\n]+?(=)', r'\1 \2 \3', text)
        # Remove param type annotations
        text = re.sub(r'\b(\w+)\s*:\s*[^,)=\n]+', r'\1', text)
        # Remove return type annotations
        text = re.sub(r'\)\s*:\s*[^\s\{=]+', ')', text)
        # Remove typed function props destructuring
        text = re.sub(r'\)\s*:\s*\{[^\}]*\}', ')', text)
        # Remove inline type casts from events
        text = re.sub(r'\(e\.currentTarget\s+as\s+[^\)]+\)', r'(e.currentTarget)', text)
        # Remove as Type cast in expressions
        text = re.sub(r'\b([A-Za-z0-9_\.\[\]]+)\s+as\s+[^\s\)\]]+', r'\1', text)
        text = re.sub(r'\b([A-Za-z0-9_\.\[\]]+)\s+as\s+[^\s\)]+', r'\1', text)
        # Remove extra type annotations from maps and arrays
        text = re.sub(r'\(\["[^"]*"[^\)]*\]\)\s*\.map', lambda m: m.group(0), text)
        # Remove type tuples like (p: any, i: number)
        text = re.sub(r'\(\s*([^)]+?)\s*\)', lambda m: '(' + re.sub(r'\b(\w+)\s*:\s*[^,=)]+', r'\1', m.group(1)) + ')', text)
        # Remove any ': Type' after closing curly in destructuring
        text = re.sub(r'\}\s*:\s*[^\s\{\(]+', '}', text)
        if text != original:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(text)
            changed_files.append(path)
for path in changed_files:
    print('fixed', os.path.relpath(path, os.getcwd()))
print('done', len(changed_files), 'files')
