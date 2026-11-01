"""Public GitHub skill discovery and reviewed, commit-pinned local installation.

Never executes downloaded code, package managers, repository hooks or installers.
The digest binds a review to exact bytes; it does not establish their safety.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

try:
    from runtime_support import atomic_write, file_lock, private_permissions, is_link
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from routing.runtime_support import atomic_write, file_lock, private_permissions, is_link

MAX_FILES = 256
MAX_FILE = 1024 * 1024
MAX_TOTAL = 8 * MAX_FILE
REPO = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')
SHA = re.compile(r'^[a-f0-9]{40}$')
NAME = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
RECEIPT = '.routing-guard-source.json'


def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Unexpected source redirect; use its canonical repository instead')


def fetch(url, limit=MAX_FILE):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in {'api.github.com', 'raw.githubusercontent.com'} or parsed.username:
        raise ValueError('Only canonical public GitHub HTTPS sources are allowed')
    request = urllib.request.Request(url, headers={'User-Agent': 'codex-routing-guard', 'Accept': 'application/json'})
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=15) as response:
            raw = response.read(limit + 1)
    except urllib.error.HTTPError as error:
        if error.code in {403, 429}:
            raise ValueError('GitHub rate limit or access restriction; keep existing skills and retry later') from error
        raise ValueError('GitHub source unavailable (HTTP ' + str(error.code) + ')') from error
    if len(raw) > limit:
        raise ValueError('Source exceeds the bounded download budget')
    return raw


def api(path):
    return json.loads(fetch('https://api.github.com/' + path, 8 * MAX_FILE))


def repository(value):
    if not REPO.fullmatch(value) or any(part in {'.', '..'} for part in value.split('/')):
        raise ValueError('Repository must be owner/name')
    return value


def safe_path(value):
    """One portable relative path; reject NT aliases even on POSIX hosts."""
    if not isinstance(value, str) or not value or '\\' in value:
        raise ValueError('Unsafe skill path')
    parts = value.split('/')
    reserved = {'CON', 'PRN', 'AUX', 'NUL', *('COM' + str(n) for n in range(1, 10)), *('LPT' + str(n) for n in range(1, 10))}
    for part in parts:
        if (not part or part in {'.', '..'} or part.endswith((' ', '.')) or
                any(ord(char) < 32 or char in '<>:"|?*' for char in part) or
                part.split('.')[0].upper() in reserved):
            raise ValueError('Unsafe or non-portable skill path: ' + value)
    return PurePosixPath(value)


def no_links(path):
    path = Path(path)
    for part in (path, *path.parents):
        if is_link(part):
            raise ValueError('Symlink/junction destination refused: ' + str(part))
    return path


def metadata(raw):
    """Read scalar discovery fields, not arbitrary YAML tags or executable content."""
    text = raw.decode('utf-8-sig')
    lines = text.splitlines()
    if not lines or lines[0] != '---' or '---' not in lines[1:]:
        raise ValueError('SKILL.md must contain YAML frontmatter')
    header = lines[1:lines[1:].index('---') + 1]
    result = {}
    for field in ('name', 'description'):
        hits = [(i, line.split(':', 1)[1].strip()) for i, line in enumerate(header) if line.startswith(field + ':')]
        if len(hits) != 1:
            raise ValueError('Skill must define one ' + field)
        index, value = hits[0]
        if value in {'>', '>-', '|', '|-'}:
            block = []
            for line in header[index + 1:]:
                if line and not line[0].isspace():
                    break
                block.append(line.strip())
            value = ' '.join(block).strip()
        elif value.startswith('"'):
            value = json.loads(value)
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1].replace("''", "'")
        elif ' #' in value:
            value = value.split(' #', 1)[0].rstrip()
        if not isinstance(value, str) or not value:
            raise ValueError('Skill metadata must use nonempty scalar strings')
        result[field] = value
    if len(result['name']) > 64 or not NAME.fullmatch(result['name']):
        raise ValueError('Skill name must be portable lowercase kebab-case')
    return result


def search(query, min_stars=1000, limit=5):
    if not query.strip() or not 1 <= limit <= 20 or min_stars < 0:
        raise ValueError('Supply a query, a nonnegative star floor and limit 1..20')
    # Stars are a candidate filter, never an approval or a safety score.
    words = re.findall(r'[^\W_]+(?:[-.][^\W_]+)*', query, re.UNICODE)
    if not words:
        raise ValueError('Search query must contain task keywords')
    keywords = ' '.join(words)
    floor = ' stars:>=' + str(min_stars) + ' archived:false'
    items = {}
    for terms in (keywords + ' skill in:name,description' + floor,
                  keywords + ' skill in:name,description,readme' + floor):
        data = api('search/repositories?' + urllib.parse.urlencode({'q': terms, 'per_page': 20}))
        for item in data['items']:
            identity = (item['full_name'] + ' ' + (item.get('description') or '')).casefold()
            if item.get('stargazers_count', 0) >= min_stars and not item.get('archived') and 'skill' in identity:
                items[item['full_name']] = item
    def relevance(item):
        text = (item['full_name'] + ' ' + (item.get('description') or '')).casefold()
        return sum(word.casefold() in text for word in words), 'skill' in text
    candidates = sorted(items.values(), key=relevance, reverse=True)[:limit]
    return {'query': query, 'minStars': min_stars, 'observedAt': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'candidates': [{key: item.get(key) for key in ('full_name', 'html_url', 'description', 'stargazers_count',
                                                         'pushed_at', 'archived', 'license')} for item in candidates],
            'note': 'Repository popularity is not skill-level quality or a security audit. Inspect exact task fit and files.'}


def catalog(repo, ref=None):
    repo = repository(repo)
    info = api('repos/' + repo)
    commit = api('repos/' + repo + '/commits/' + urllib.parse.quote(ref or info['default_branch'], safe=''))['sha']
    if not SHA.fullmatch(commit):
        raise ValueError('GitHub did not return a full commit SHA')
    tree = api('repos/' + repo + '/git/trees/' + commit + '?recursive=1')
    if tree.get('truncated'):
        raise ValueError('Repository tree is truncated')
    paths = [item['path'] for item in tree['tree'] if item['type'] == 'blob' and
             (item['path'] == 'SKILL.md' or item['path'].endswith('/SKILL.md'))]
    return {'repository': repo, 'commit': commit, 'starsObserved': info.get('stargazers_count'),
            'skillPaths': sorted({str(PurePosixPath(path).parent) for path in paths})[:1000],
            'totalSkills': len(paths), 'note': 'Paths are candidates, not endorsements; inspect the selected skill.'}


def prepare(repo, skill_path, stage, task, benefit, ref=None, min_stars=1000):
    repo = repository(repo)
    prefix = str(safe_path(skill_path)) if skill_path != '.' else ''
    if not task.strip() or not benefit.strip() or min_stars < 0:
        raise ValueError('Task, expected quality benefit and nonnegative star floor are required')
    stage = no_links(Path(stage).expanduser().absolute()).resolve()
    if stage.exists():
        raise ValueError('Review stage must be a new directory')
    info = api('repos/' + repo)
    if info.get('archived') or info.get('disabled') or info.get('private') or info.get('stargazers_count', 0) < min_stars:
        raise ValueError('Source is archived, unavailable, private or below the requested popularity floor')
    license_info = info.get('license') or {}
    if not license_info.get('spdx_id') or license_info['spdx_id'] == 'NOASSERTION':
        raise ValueError('Source license is unknown; resolve reuse permission before installation')
    commit = api('repos/' + repo + '/commits/' + urllib.parse.quote(ref or info['default_branch'], safe=''))['sha']
    if not SHA.fullmatch(commit):
        raise ValueError('GitHub did not return a full commit SHA')
    tree = api('repos/' + repo + '/git/trees/' + commit + '?recursive=1')
    if tree.get('truncated'):
        raise ValueError('Repository tree is truncated; exact skill inventory could not be verified')
    files = []
    seen = set()
    for item in tree['tree']:
        path = item['path']
        if prefix and not path.startswith(prefix + '/'):
            continue
        relative = path[len(prefix) + 1:] if prefix else path
        if item['type'] == 'tree':
            continue
        safe_path(relative)
        if item.get('mode') not in {'100644', '100755'} or item['type'] != 'blob':
            raise ValueError('Symlinks, submodules and special files are not installable')
        if (relative.casefold() in seen or relative.casefold() in {RECEIPT, 'review.json'} or
                relative.casefold().startswith('.routing-guard-licenses/')):
            raise ValueError('Case-colliding or reserved skill filename')
        seen.add(relative.casefold())
        if item.get('size', MAX_FILE + 1) > MAX_FILE:
            raise ValueError('Skill file exceeds 1 MiB download budget')
        files.append((relative, path, 0o700 if item['mode'] == '100755' else 0o600))
    licenses = {}
    if prefix:
        for item in tree['tree']:
            path = item['path']
            if '/' not in path and re.fullmatch(r'(?:licen[sc]e(?:[._-].+)?|copying)', path, re.I):
                safe_path(path)
                if item.get('mode') not in {'100644', '100755'} or item.get('size', MAX_FILE + 1) > MAX_FILE:
                    raise ValueError('Source license must be a bounded regular file')
                relative = '.routing-guard-licenses/' + path
                files.append((relative, path, 0o600))
                licenses[relative] = path
        if not licenses:
            raise ValueError('Source license text could not be preserved; review the repository license layout')
    if not files or len(files) > MAX_FILES or 'SKILL.md' not in {f[0] for f in files}:
        raise ValueError('Select a skill folder containing SKILL.md and at most 256 files')
    downloaded = {}
    total = 0
    modes = {}
    for relative, source, mode in sorted(files):
        raw = fetch('https://raw.githubusercontent.com/' + repo + '/' + commit + '/' + urllib.parse.quote(source, safe='/'))
        total += len(raw)
        if total > MAX_TOTAL:
            raise ValueError('Skill bundle exceeds 8 MiB download budget')
        downloaded[relative] = raw
        modes[relative] = mode
    meta = metadata(downloaded['SKILL.md'])
    manifest = {'schemaVersion': 1, 'repository': repo, 'commit': commit, 'skillPath': skill_path, **meta,
                'sourceUrl': 'https://github.com/' + repo + '/tree/' + commit + ('/' + prefix if prefix else ''),
                'starsObserved': info['stargazers_count'], 'license': info['license']['spdx_id'],
                'pushedAt': info.get('pushed_at'), 'observedAt': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'task': task, 'expectedBenefit': benefit,
                'files': {name: hashlib.sha256(raw).hexdigest() for name, raw in downloaded.items()},
                'fileModes': modes, 'licenseFiles': licenses}
    stage.parent.mkdir(parents=True, exist_ok=True)
    # No stage is published until all network reads and metadata checks succeed.
    with tempfile.TemporaryDirectory(prefix='.skill-review-', dir=stage.parent) as temporary:
        bundle = Path(temporary) / 'bundle'
        bundle.mkdir()
        private_permissions(bundle, 0o700)
        for name, raw in downloaded.items():
            atomic_write(bundle / name, raw, modes[name])
        atomic_write(bundle / 'review.json', (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
        bundle.rename(stage)
    return {'stage': str(stage), 'reviewDigest': digest(manifest), 'manifest': manifest,
            'next': 'Read every instruction and executable/reference file in this stage before installing. Do not run them.'}


def inspect_stage(stage):
    stage = no_links(Path(stage).expanduser().absolute()).resolve()
    no_links(stage / 'review.json')
    manifest = json.loads((stage / 'review.json').read_text(encoding='utf-8'))
    if manifest.get('schemaVersion') != 1 or not SHA.fullmatch(manifest.get('commit', '')):
        raise ValueError('Unsupported review manifest')
    repository(manifest['repository'])
    name = manifest['name']
    if not NAME.fullmatch(name) or len(name) > 64:
        raise ValueError('Invalid skill destination name')
    files = manifest['files']
    if not isinstance(files, dict) or not 1 <= len(files) <= MAX_FILES or 'SKILL.md' not in files:
        raise ValueError('Invalid file inventory')
    inventory = set()
    for path in stage.rglob('*'):
        no_links(path)
        if path.is_file():
            inventory.add(path.relative_to(stage).as_posix())
    if inventory != set(files) | {'review.json'}:
        raise ValueError('Review stage has untracked or missing files')
    total = 0
    modes = manifest.get('fileModes', {name: 0o600 for name in files})
    if set(modes) != set(files) or any(mode not in {0o600, 0o700} for mode in modes.values()):
        raise ValueError('Invalid skill file modes')
    seen = set()
    for relative, expected in files.items():
        safe_path(relative)
        if relative.casefold() in {'review.json', RECEIPT} or relative.casefold() in seen:
            raise ValueError('Reserved or case-colliding filename')
        seen.add(relative.casefold())
        path = stage / relative
        if os.name != 'nt' and path.stat().st_mode & 0o777 != modes[relative]:
            raise ValueError('Staged file permissions changed since preparation')
        if path.stat().st_size > MAX_FILE:
            raise ValueError('Staged file exceeds review budget')
        raw = path.read_bytes()
        total += len(raw)
        if total > MAX_TOTAL or hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('Review bytes changed; inspect and prepare a new pinned stage')
    if metadata((stage / 'SKILL.md').read_bytes()) != {key: manifest[key] for key in ('name', 'description')}:
        raise ValueError('Skill discovery metadata differs from manifest')
    return stage, manifest


def install(stage, home, reviewed_digest, apply=False):
    stage, manifest = inspect_stage(stage)
    if reviewed_digest != digest(manifest):
        raise ValueError('Exact review digest required; installation is not an automatic trust decision')
    home = no_links(Path(home).expanduser().absolute()).resolve()
    destination = no_links(home / 'skills' / manifest['name'])
    if destination.exists():
        raise ValueError('Existing skill preserved; no overwrite or implicit upgrade')
    result = {'installed': False, 'destination': str(destination), 'repository': manifest['repository'],
              'commit': manifest['commit'], 'reviewDigest': reviewed_digest, 'codeExecuted': False}
    if not apply:
        return result
    destination.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(home / '.skill-supply.lock'):
        no_links(destination)
        if destination.exists():
            raise ValueError('Existing skill preserved; concurrent installation detected')
        # Recheck under lock and check hashes again while copying into a private bundle.
        _, current = inspect_stage(stage)
        if digest(current) != reviewed_digest:
            raise ValueError('Review manifest changed before install')
        with tempfile.TemporaryDirectory(prefix='.skill-install-', dir=destination.parent) as temporary:
            bundle = Path(temporary) / 'bundle'
            bundle.mkdir()
            private_permissions(bundle, 0o700)
            for relative, expected in current['files'].items():
                raw = no_links(stage / relative).read_bytes()
                if hashlib.sha256(raw).hexdigest() != expected:
                    raise ValueError('Reviewed source changed during install')
                atomic_write(bundle / relative, raw, current.get('fileModes', {}).get(relative, 0o600))
            atomic_write(bundle / RECEIPT, (json.dumps({**result, 'installed': True}, indent=2) + '\n').encode('utf-8'))
            no_links(destination)
            if destination.exists():
                raise ValueError('Destination appeared during installation')
            bundle.rename(destination)
    return {**result, 'installed': True, 'activation': 'Available on the next turn after host discovery; no live execution claimed.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('search')
    p.add_argument('--query', required=True)
    p.add_argument('--min-stars', type=int, default=1000)
    p.add_argument('--limit', type=int, default=5)
    p = sub.add_parser('catalog')
    p.add_argument('--repo', required=True)
    p.add_argument('--ref')
    p = sub.add_parser('prepare')
    for field in ('repo', 'path', 'stage', 'task', 'benefit'):
        p.add_argument('--' + field, required=True)
    p.add_argument('--ref')
    p.add_argument('--min-stars', type=int, default=1000)
    p = sub.add_parser('inspect')
    p.add_argument('--stage', required=True)
    p = sub.add_parser('install')
    p.add_argument('--stage', required=True)
    p.add_argument('--reviewed-digest', required=True)
    p.add_argument('--codex-home', default=os.environ.get('CODEX_HOME') or str(Path.home() / '.codex'))
    p.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        if args.command == 'search':
            result = search(args.query, args.min_stars, args.limit)
        elif args.command == 'catalog':
            result = catalog(args.repo, args.ref)
        elif args.command == 'prepare':
            result = prepare(args.repo, args.path, args.stage, args.task, args.benefit, args.ref, args.min_stars)
        elif args.command == 'inspect':
            stage, manifest = inspect_stage(args.stage)
            result = {'stage': str(stage), 'reviewDigest': digest(manifest), 'manifest': manifest}
        else:
            result = install(args.stage, args.codex_home, args.reviewed_digest, args.apply)
        print(json.dumps(result, ensure_ascii=True, indent=2))
    except (ValueError, KeyError, TypeError, OSError, TimeoutError, urllib.error.URLError) as error:
        parser.exit(2, str(error) + '\n')


if __name__ == '__main__':
    main()
