import os, re

path = 'C:/Users/nisha/.gemini/antigravity/scratch/ios-pipa/tools/sweet-root-tweaks.py'
content = open(path, 'r', encoding='utf-8').read()

# Also ensure /private/var/datamigrator/migrate is deleted
target_pref = '''def merge_host_pref(root, name, values):
    """Merge keys into the ByHost NAME.<platform UUID>.plist files (upper and lower case UUID)."""
    d = os.path.join(root, 'private/var/mobile/Library/Preferences/ByHost')
    if not os.path.isdir(d):
        os.makedirs(d)
        os.chown(d, MOBILE, MOBILE)
        os.chmod(d, 0o755)
    host = platform_uuid()
    for u in (host, host.lower()):
        path = os.path.join(d, f'{name}.{u}.plist')
        data = load(path) if os.path.exists(path) else {}
        data.update(values)
        save(path, data, MOBILE, MOBILE, 0o600)
    print('host pref', name, host, sorted(values))'''

replacement_pref = '''def merge_host_pref(root, name, values):
    """Merge keys into the ByHost NAME.<platform UUID>.plist files (upper and lower case UUID)."""
    host = platform_uuid()
    for home, uid in (('mobile', MOBILE), ('root', 0)):
        d = os.path.join(root, f'private/var/{home}/Library/Preferences/ByHost')
        if not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
            os.chown(d, uid, uid)
            os.chmod(d, 0o755)
        for u in (host, host.lower()):
            path = os.path.join(d, f'{name}.{u}.plist')
            data = load(path) if os.path.exists(path) else {}
            data.update(values)
            save(path, data, uid, uid, 0o600)
    # Also remove datamigrator sentinel if present
    sentinel = os.path.join(root, 'private/var/datamigrator/migrate')
    if os.path.exists(sentinel):
        try:
            os.remove(sentinel)
            print('Removed datamigrator migrate sentinel')
        except Exception as e:
            print('Could not remove sentinel:', e)
    print('host pref', name, host, sorted(values))'''

# Normalize newlines
content_norm = content.replace('\r\n', '\n')
target_norm = target_pref.replace('\r\n', '\n')
replacement_norm = replacement_pref.replace('\r\n', '\n')

if target_norm in content_norm:
    content_norm = content_norm.replace(target_norm, replacement_norm)
    open(path, 'w', encoding='utf-8', newline='\n').write(content_norm)
    print('SUCCESS')
else:
    print('TARGET NOT FOUND')
