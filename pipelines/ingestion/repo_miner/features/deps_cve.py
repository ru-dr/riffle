"""
deps_cve.py — Source 4e dependency / CVE risk, only when a manifest/lockfile
changes.

Categorizes each dependency change (added / removed / upgraded / downgraded),
then queries OSV.dev for the vulnerabilities affecting the before- and after-
versions so we can report both what a PR *introduces* and what it *fixes*. OSV
already aggregates the GitHub Advisory Database (GHSA), so severity and malware
flags are derived from the OSV records; a separate GHSA GraphQL call is used
only for the one signal that genuinely needs a second source — the OSV-vs-GHSA
disagreement check.

Network calls are behind Settings.enable_deps_cve. Everything degrades to None
(treated as missing) if the network or a parser is unavailable; the dependency
*counts* need no network and are always computed.
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

import config
import gitio

OSV_QUERY = "https://api.osv.dev/v1/query"
GITHUB_GRAPHQL = "https://api.github.com/graphql"

# minimal ecosystem inference from manifest filename
ECOSYSTEM = {
    "package.json": "npm", "package-lock.json": "npm", "yarn.lock": "npm",
    "requirements.txt": "PyPI", "pyproject.toml": "PyPI", "poetry.lock": "PyPI",
    "go.mod": "Go", "go.sum": "Go", "Cargo.toml": "crates.io", "Cargo.lock": "crates.io",
    "Gemfile": "RubyGems", "Gemfile.lock": "RubyGems", "pom.xml": "Maven",
    "composer.json": "Packagist", "composer.lock": "Packagist",
    "pubspec.yaml": "Pub", "pubspec.lock": "Pub",
    "mix.exs": "Hex", "mix.lock": "Hex",
    # NuGet is by extension (*.csproj / .vbproj / .fsproj); see _ecosystem_of
}


def _ecosystem_of(path: str) -> str | None:
    """OSV ecosystem for a manifest path — exact filename, else extension."""
    name = path.split("/")[-1]
    if name in ECOSYSTEM:
        return ECOSYSTEM[name]
    if re.fullmatch(r"requirements[^/]*\.txt", name):
        return "PyPI"
    if name == "Pipfile.lock":
        return "PyPI"
    if name.lower().endswith((".csproj", ".vbproj", ".fsproj")):
        return "NuGet"
    return None


# OSV ecosystem string -> GHSA GraphQL ecosystem enum
_GHSA_ECO = {
    "npm": "NPM", "PyPI": "PIP", "Go": "GO", "crates.io": "RUST",
    "RubyGems": "RUBYGEMS", "Maven": "MAVEN", "Packagist": "COMPOSER",
    "NuGet": "NUGET", "Pub": "PUB", "Hex": "ERLANG",
}

_SEVERITY_RANK = {"LOW": 1, "MODERATE": 2, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
_RANK_SEVERITY = {1: "LOW", 2: "MODERATE", 3: "HIGH", 4: "CRITICAL"}

_COUNT_KEYS = ("n_deps_added", "n_deps_upgraded", "n_deps_downgraded", "n_major_bumps")
_CVE_KEYS = ("osv_vulns_introduced", "osv_vulns_fixed", "ghsa_max_severity_introduced",
             "ghsa_malware_dep", "vuln_has_fix_available", "osv_ghsa_disagreement")

_EMPTY = {
    "n_deps_added": 0, "n_deps_upgraded": 0, "n_deps_downgraded": 0,
    "n_major_bumps": 0, "osv_vulns_introduced": None, "osv_vulns_fixed": None,
    "ghsa_max_severity_introduced": None, "ghsa_malware_dep": None,
    "vuln_has_fix_available": None, "osv_ghsa_disagreement": None,
}

# lockfiles carry the resolved version, so within one directory they win over the manifest's range
_LOCKFILES = {"package-lock.json", "yarn.lock", "poetry.lock", "go.sum", "Cargo.lock",
              "Gemfile.lock", "composer.lock", "pubspec.lock", "mix.lock", "Pipfile.lock"}

_UNSET = object()


def _to_dt(v):
    """A timezone-aware datetime from a datetime or an ISO-8601 string; None if it cannot be read."""
    if v is None:
        return None
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(v, datetime):
        return None
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


def _read_at(repo: str, sha: str, path: str):
    """(text, observed). text None with observed True = the path is not in that commit; observed
    False = git could not be read, which is not the same as an empty file (every dependency would
    look removed or added)."""
    text = gitio.file_content_at(repo, sha, path)
    if text is not None:
        return text, True
    try:
        proc = subprocess.run(["git", "-C", repo, "ls-tree", sha, "--", path],
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None, False
    if proc.returncode != 0:
        return None, False
    return None, not proc.stdout.strip()        # listed but unreadable = unobserved


def _scope_of(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def compute(repo: str, base: str, head: str, changed: list[str],
            st: config.Settings, as_of=None, renames: dict | None = None) -> dict:
    """as_of: the moment the PR is scored at (a datetime or ISO string). With it, only advisories
    published by then, and not withdrawn, count, and anything that cannot be dated is null; without
    it the lookup uses today's OSV data. renames: {new_path: old_path} so a manifest renamed in the
    PR is read at its old path on the base side."""
    manifests = [p for p in changed
                 if config.path_flags(p)["touches_dep_manifest"]
                 or config.path_flags(p)["touches_lockfile"]]
    if not manifests:
        return dict(_EMPTY)
    renames = renames or {}

    # --- categorize every dependency change ---------------------------------
    # per (ecosystem, directory): the dependencies at base and at head. A monorepo's workspaces
    # share names, so the directory is part of the key; a lockfile's resolved version beats the
    # manifest's range in the same directory.
    scopes: dict[tuple, list] = {}
    unparsed = False
    for m in sorted(manifests, key=lambda p: (p.rsplit("/", 1)[-1] in _LOCKFILES, p)):
        eco = _ecosystem_of(m)
        if not eco:
            unparsed = True
            continue
        sides = []
        for sha, path in ((base, renames.get(m, m)), (head, m)):
            text, observed = _read_at(repo, sha, path)
            if not observed:
                unparsed = True
                sides.append({})
            elif text is None:
                sides.append({})                # not in that commit: no dependencies there
            else:
                parsed = _parse_file(text, eco, path)
                if parsed is None:
                    unparsed = True             # a manifest we cannot read is unobserved, not empty
                    parsed = {}
                sides.append(parsed)
        sc = scopes.setdefault((eco, _scope_of(m)), [{}, {}])
        sc[0].update(sides[0])
        sc[1].update(sides[1])

    added, removed, upgraded, downgraded = {}, {}, {}, {}
    unresolved = False
    for (eco, scope), (b, h) in scopes.items():
        for name, hv in h.items():
            if name not in b:
                added[(eco, name, scope)] = (None, hv)
                unresolved = unresolved or hv is None
            elif b[name] != hv:
                if b[name] is None or hv is None:
                    unresolved = True           # a version we cannot read changed: cannot classify
                    continue
                c = _cmp(_vkey(hv), _vkey(b[name]))
                if c > 0:
                    upgraded[(eco, name, scope)] = (b[name], hv)
                elif c < 0:
                    downgraded[(eco, name, scope)] = (b[name], hv)
        for name, bv in b.items():
            if name not in h:
                removed[(eco, name, scope)] = (bv, None)
                unresolved = unresolved or bv is None

    result = dict(_EMPTY)
    if unparsed:
        for k in _COUNT_KEYS:
            result[k] = None                    # an unread manifest makes every count unobserved
        return result
    result["n_deps_added"] = len(added)
    result["n_deps_upgraded"] = len(upgraded)
    result["n_deps_downgraded"] = len(downgraded)
    result["n_major_bumps"] = sum(
        1 for (bv, hv) in upgraded.values() if _major_changed(bv, hv))

    if not st.enable_deps_cve or unresolved:
        return result                           # unresolved versions leave the CVE fields null

    # --- CVE diff via OSV ---------------------------------------------------
    # every dep whose version set changed, with its before/after versions
    changes = {**added, **removed, **upgraded, **downgraded}
    # collect the concrete (eco, name, version) points we must query
    to_query = set()
    for (eco, name, _scope), (bv, hv) in changes.items():
        if bv:
            to_query.add((eco, name, bv))
        if hv:
            to_query.add((eco, name, hv))

    vulns_at, failed = _osv_query_all(to_query, st)  # (results, failed points)
    if to_query and failed:
        # at least one dependency version could not be queried (network / OSV
        # rate limit / bad response). Its vulns are UNOBSERVED, so computing the
        # aggregates now would present an undercount as a reassuring number.
        # Leave the CVE fields None (unobserved), exactly as for a total outage
        # — never score an unqueried dependency as 0/clean. Counts stay set.
        return result

    when = _to_dt(as_of) if as_of is not None else None
    if as_of is not None and when is None:
        return result                           # an as-of we cannot read: do not fall back to today
    if when is not None:
        vulns_at = _as_of_filter(vulns_at, when)
        if vulns_at is None:
            return result                       # an advisory with no usable date: unobserved

    introduced_ids, fixed_ids = set(), set()
    introduced_records = []
    for (eco, name, _scope), (bv, hv) in changes.items():
        base_v = vulns_at.get((eco, name, bv), []) if bv else []
        head_v = vulns_at.get((eco, name, hv), []) if hv else []
        base_ids = {v.get("id") for v in base_v}
        head_ids = {v.get("id") for v in head_v}
        new_here = head_ids - base_ids
        introduced_ids |= new_here
        fixed_ids |= (base_ids - head_ids)
        introduced_records.extend(v for v in head_v if v.get("id") in new_here)

    result["osv_vulns_introduced"] = len(introduced_ids)
    result["osv_vulns_fixed"] = len(fixed_ids)
    if not introduced_records:
        result["vuln_has_fix_available"] = False
    elif when is None:
        result["vuln_has_fix_available"] = any(_has_fix(v) for v in introduced_records)
    elif any(_has_fix(v) for v in introduced_records):
        result["vuln_has_fix_available"] = None  # OSV does not date a fix's release: unknown then
    else:
        result["vuln_has_fix_available"] = False  # no fix exists even today, so none existed then

    # severity + malware derived from the introduced OSV records (GHSA-sourced)
    sev_rank = 0
    malware = False
    for v in introduced_records:
        sev_rank = max(sev_rank, _severity_rank(v))
        malware = malware or _is_malware(v)
    result["ghsa_max_severity_introduced"] = _RANK_SEVERITY.get(sev_rank)
    result["ghsa_malware_dep"] = malware if introduced_records else False

    # --- OSV vs GHSA disagreement (needs a real GHSA query) -----------------
    # only meaningful for versions newly present at head (added + upgraded)
    head_points = [((eco, name), hv)
                   for (eco, name, _s), (bv, hv) in {**added, **upgraded}.items() if hv]
    result["osv_ghsa_disagreement"] = _osv_ghsa_disagreement(head_points, vulns_at, st, when)
    return result


def _as_of_filter(vulns_at: dict, when: datetime):
    """vulns_at without the advisories that did not exist at `when` (published later) or that were
    withdrawn. None if any advisory cannot be dated, since whether it counts is then unknown."""
    out = {}
    for point, recs in vulns_at.items():
        keep = []
        for v in recs:
            if v.get("withdrawn"):
                continue
            pub = _to_dt(v.get("published"))
            if pub is None:
                return None
            if pub <= when:
                keep.append(v)
        out[point] = keep
    return out


# --------------------------------------------------------------------------
# manifest parsing (best-effort, per ecosystem)
# --------------------------------------------------------------------------
_VERSION_RE = r"\d+(?:\.\d+){0,3}(?:-[0-9A-Za-z.\-]+)?(?:\+[0-9A-Za-z.\-]+)?"


def _concrete(spec) -> str | None:
    """The version in a single-bound npm spec (`1.2.3`, `^1.2.3`, `>=1.2`); None for anything that
    is not one concrete version (a range, `*`, `1.x`, `workspace:*`, a git URL, an `npm:` alias)."""
    m = re.fullmatch(r"[\^~>=<v\s]*(" + _VERSION_RE + r")", str(spec).strip())
    return m.group(1) if m else None


def _put(deps: dict, name: str, ver) -> None:
    """Record name -> version. A lockfile can hold several versions of one name; keep the highest,
    so the result does not depend on the order of the entries."""
    if not ver:
        ver = None
    if name not in deps:
        deps[name] = ver
    elif ver is not None and (deps[name] is None or _cmp(_vkey(ver), _vkey(deps[name])) > 0):
        deps[name] = ver


def _norm_py(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()       # PEP 503


def _parse_manifest(text: str, eco: str) -> dict[str, str]:
    """Compatibility wrapper: {} where _parse_file cannot read the text."""
    return _parse_file(text, eco, None) or {}


def _parse_file(text: str, eco: str, path: str | None) -> dict | None:
    """{name: version} for one manifest or lockfile; a version is None when the file names the
    dependency without a concrete one. None (not {}) when the file cannot be read: it is invalid,
    or is a format this parser does not cover, so its dependencies are unobserved."""
    base = (path or "").rsplit("/", 1)[-1]
    try:
        deps = _parse_by_kind(text, eco, base)
    except (ValueError, TypeError, AttributeError, KeyError):
        return None
    if deps is None:
        return None
    return {k: (v or None) for k, v in deps.items()}


def _parse_by_kind(text: str, eco: str, base: str) -> dict | None:
    deps: dict = {}
    if eco == "npm":
        if base == "package-lock.json":
            return _parse_package_lock(json.loads(text))
        if base == "yarn.lock":
            return _parse_yarn_lock(text)
        if base not in ("package.json", ""):
            return None
        data = json.loads(text)
        if not isinstance(data, dict):
            return None
        for sect in ("dependencies", "devDependencies", "optionalDependencies"):
            for name, ver in (data.get(sect) or {}).items():
                _put(deps, name, _concrete(ver))
    elif eco == "PyPI":
        if base in ("pyproject.toml",):
            return None
        if base == "poetry.lock":
            return {_norm_py(k): v for k, v in _parse_cargo(text).items()}
        if base == "Pipfile.lock":
            data = json.loads(text)
            for sect in ("default", "develop"):
                for name, meta in (data.get(sect) or {}).items():
                    v = str((meta or {}).get("version", "")).lstrip("=")
                    _put(deps, _norm_py(name), v if re.fullmatch(_VERSION_RE, v) else None)
            return deps
        for line in text.splitlines():
            m = re.match(r"^\s*([A-Za-z0-9_.\-]+)\s*(?:\[[^\]]*\])?\s*===?\s*([0-9][^\s;,#]*)", line)
            if m:
                _put(deps, _norm_py(m.group(1)), m.group(2))
    elif eco == "Go":
        for line in text.splitlines():
            line = line.split("//", 1)[0].strip()
            if line.startswith(("replace", "exclude", "retract", "module", "go ", "toolchain")):
                continue
            line = re.sub(r"^require\s+", "", line)
            m = re.match(r"^([^\s(]+)\s+v([0-9][^\s]*)", line)
            if m:
                ver = m.group(2)
                ver = ver[:-len("/go.mod")] if ver.endswith("/go.mod") else ver     # go.sum has two lines each
                _put(deps, m.group(1), ver.replace("+incompatible", ""))
    elif eco == "Maven":
        if base not in ("pom.xml", ""):
            return None
        parsed = _parse_pom(text)
        if parsed is None:
            return None
        deps.update(parsed)
    elif eco == "RubyGems":
        deps.update(_parse_ruby(text))
    elif eco == "crates.io":
        deps.update(_parse_cargo(text))
    elif eco == "Packagist":
        if base == "composer.lock":
            data = json.loads(text)
            for sect in ("packages", "packages-dev"):
                for pkg in data.get(sect) or []:
                    _put(deps, pkg["name"], _clean_ver(str(pkg.get("version", "")).lstrip("v")))
            return deps
        parsed = _parse_composer(text)
        if parsed is None:
            return None
        deps.update(parsed)
    elif eco == "NuGet":
        deps.update(_parse_csproj(text))
    elif eco == "Pub":
        if base == "pubspec.lock":
            return None
        deps.update(_parse_pubspec(text))
    elif eco == "Hex":
        if base == "mix.lock":
            return None
        deps.update(_parse_mix(text))
    else:
        return None             # other ecosystems: unparsed, so unobserved
    return deps


def _parse_package_lock(data) -> dict | None:
    """package-lock.json: lockfileVersion 2/3 `packages` (keyed by node_modules path), else the
    v1 nested `dependencies` tree."""
    if not isinstance(data, dict):
        return None
    deps: dict = {}
    if isinstance(data.get("packages"), dict):
        for key, meta in data["packages"].items():
            if not key or not isinstance(meta, dict) or meta.get("link"):
                continue
            name = key.rsplit("node_modules/", 1)[-1]
            if "node_modules/" not in key:
                continue                         # a workspace folder, not an installed package
            ver = meta.get("version")
            _put(deps, name, ver if isinstance(ver, str) else None)
        return deps
    if isinstance(data.get("dependencies"), dict):
        def walk(tree):
            for name, meta in tree.items():
                if not isinstance(meta, dict):
                    continue
                ver = meta.get("version")
                _put(deps, name, ver if isinstance(ver, str) and re.fullmatch(_VERSION_RE, ver) else None)
                if isinstance(meta.get("dependencies"), dict):
                    walk(meta["dependencies"])
        walk(data["dependencies"])
        return deps
    return deps if data.get("lockfileVersion") else None


def _parse_yarn_lock(text: str) -> dict | None:
    """yarn.lock v1 and berry: `"name@spec", name@spec2:` header, then `  version "1.2.3"`."""
    deps: dict = {}
    names: list[str] = []
    for line in text.splitlines():
        if line and not line[0].isspace() and not line.startswith("#") and line.rstrip().endswith(":"):
            names = []
            for part in line.rstrip()[:-1].split(","):
                part = part.strip().strip('"')
                at = part.rfind("@")
                if at > 0:
                    names.append(part[:at])
            continue
        m = re.match(r'^\s+version:?\s+"?([^"\s]+)"?\s*$', line)
        if m and names:
            for n in names:
                _put(deps, n, m.group(1) if re.fullmatch(_VERSION_RE, m.group(1)) else None)
            names = []
    if not deps and text.strip() and "version" in text:
        return None
    return deps


def _clean_ver(v) -> str:
    """Extract the first concrete version token from a spec (drops ^ ~ >= etc.)."""
    m = re.search(r"[0-9][0-9A-Za-z.\-+]*", str(v))
    return m.group(0) if m else ""


def _parse_ruby(text: str) -> dict[str, str]:
    """Gemfile (`gem 'x', '~> 1.2'`) and Gemfile.lock (`    x (1.2.3)`)."""
    deps: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"^\s*gem\s+['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]", line)
        if m:
            deps[m.group(1)] = _clean_ver(m.group(2))
            continue
        m = re.match(r"^\s{2,}([A-Za-z0-9_\-]+)\s+\(([0-9][^)]*)\)\s*$", line)
        if m:
            deps[m.group(1)] = _clean_ver(m.group(2))
    return deps


def _parse_cargo(text: str) -> dict[str, str]:
    """Cargo.toml ([dependencies] sections) and Cargo.lock ([[package]] blocks)."""
    deps: dict[str, str] = {}
    section = None
    for line in text.splitlines():
        s = line.strip()
        hm = re.match(r"^\[([^\]]+)\]", s)
        if hm:
            section = hm.group(1)
            continue
        if section and "dependencies" in section:
            m = re.match(r"^([A-Za-z0-9_\-]+)\s*=\s*\"([^\"]+)\"", s)
            if m:
                deps[m.group(1)] = _clean_ver(m.group(2))
                continue
            m = re.match(r"^([A-Za-z0-9_\-]+)\s*=\s*\{.*version\s*=\s*\"([^\"]+)\"", s)
            if m:
                deps[m.group(1)] = _clean_ver(m.group(2))
    if "[[package]]" in text:                       # Cargo.lock
        for blk in text.split("[[package]]"):
            nm = re.search(r"name\s*=\s*\"([^\"]+)\"", blk)
            vr = re.search(r"version\s*=\s*\"([^\"]+)\"", blk)
            if nm and vr:
                _put(deps, nm.group(1), _clean_ver(vr.group(1)))
    return deps


def _parse_composer(text: str) -> dict[str, str] | None:
    """composer.json require / require-dev ({"vendor/pkg": "^1.2"})."""
    deps: dict[str, str] = {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    for sect in ("require", "require-dev"):
        for name, ver in (data.get(sect) or {}).items():
            low = name.lower()
            if low == "php" or low.startswith(("ext-", "lib-")) or "/" not in name:
                continue                            # platform / pseudo-packages
            v = _clean_ver(ver)
            if v:
                deps[name] = v
    return deps


def _parse_csproj(text: str) -> dict[str, str]:
    """.NET .csproj <PackageReference Include="X" Version="1.2.3" /> (attr order-free)."""
    deps: dict[str, str] = {}
    for m in re.finditer(r"<PackageReference\b([^>]*?)/?>", text):
        attrs = m.group(1)
        inc = re.search(r'Include="([^"]+)"', attrs)
        ver = re.search(r'Version="([^"]+)"', attrs)
        if inc and ver:
            deps[inc.group(1)] = _clean_ver(ver.group(1))
    # element form: <PackageReference Include="X"><Version>1.2</Version>...
    for m in re.finditer(
            r'<PackageReference\b[^>]*?Include="([^"]+)"[^>]*?>.*?<Version>([^<]+)</Version>',
            text, re.S):
        deps.setdefault(m.group(1), _clean_ver(m.group(2)))
    return deps


def _parse_pubspec(text: str) -> dict[str, str]:
    """Dart pubspec.yaml dependencies / dev_dependencies (`name: ^1.2.3`)."""
    deps: dict[str, str] = {}
    in_dep = False
    for line in text.splitlines():
        if re.match(r"^(dependencies|dev_dependencies):\s*$", line):
            in_dep = True
            continue
        if re.match(r"^\S", line):                  # dedent -> section ended
            in_dep = False
        if in_dep:
            m = re.match(r"^\s+([A-Za-z0-9_]+):\s*[\^~>=<\s]*([0-9][^\s#]*)", line)
            if m:
                deps[m.group(1)] = _clean_ver(m.group(2))
    return deps


def _parse_mix(text: str) -> dict[str, str]:
    """Elixir mix.exs deps ({:phoenix, "~> 1.6"}); git/path deps have no version."""
    deps: dict[str, str] = {}
    for m in re.finditer(r"\{\s*:([A-Za-z0-9_]+)\s*,\s*\"([^\"]*)\"", text):
        v = _clean_ver(m.group(2))
        if v:
            deps[m.group(1)] = v
    return deps


def _parse_pom(text: str) -> dict[str, str] | None:
    """
    Parse a Maven pom.xml into {"groupId:artifactId": version} — the OSV/GHSA
    key format for the Maven ecosystem. Resolves ${property} versions from the
    <properties> block. Dependencies whose version is inherited from a parent
    POM or an unresolved property are skipped (we can't query those locally).
    """
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None

    def local(tag: str) -> str:                 # strip XML namespace
        return tag.rsplit("}", 1)[-1]

    # collect <properties> for ${...} substitution
    props: dict[str, str] = {}
    for el in root.iter():
        if local(el.tag) == "properties":
            for child in el:
                props[local(child.tag)] = (child.text or "").strip()

    deps: dict[str, str] = {}
    for dep in root.iter():
        if local(dep.tag) != "dependency":
            continue
        gid = aid = ver = None
        for child in dep:
            t, v = local(child.tag), (child.text or "").strip()
            if t == "groupId":
                gid = v
            elif t == "artifactId":
                aid = v
            elif t == "version":
                ver = v
        if not gid or not aid or not ver:
            continue                            # missing coords / parent-inherited version
        m = re.fullmatch(r"\$\{([^}]+)\}", ver)  # resolve a ${property} version
        if m:
            ver = props.get(m.group(1), "")
            if not ver or ver.startswith("${"):
                continue                        # unresolved property
        if re.match(r"\d", ver):                # concrete version only
            deps[f"{gid}:{aid}"] = ver
    return deps


# --------------------------------------------------------------------------
# version helpers
# --------------------------------------------------------------------------
def _semver_tuple(v: str | None):
    if not v:
        return None
    core = v.split("+")[0].split("-")[0]
    nums = re.findall(r"\d+", core)
    if not nums:
        return None
    parts = [int(x) for x in nums[:3]]
    parts += [0] * (3 - len(parts))
    return tuple(parts)


def _vkey(v: str | None):
    """A sort key for a version: every numeric part counts (1.2.3.4 > 1.2.3), trailing zeros do not
    (1.0 == 1.0.0), and a pre-release sorts before its release (1.0.0-rc1 < 1.0.0). None if unreadable."""
    if not v:
        return None
    v = str(v).split("+")[0]
    core, _, pre = v.partition("-")
    nums = [int(x) for x in re.findall(r"\d+", core)]
    if not nums:
        return None
    while len(nums) > 1 and nums[-1] == 0:
        nums.pop()
    if not pre:
        return (tuple(nums), (1,))
    idents = tuple((0, int(i), "") if i.isdigit() else (1, 0, i) for i in re.split(r"[.\-]", pre) if i)
    return (tuple(nums), (0, idents))


def _cmp(a, b) -> int:
    if a is None or b is None:
        return 0
    return (a > b) - (a < b)


def _major_changed(bv: str, hv: str) -> bool:
    bt, ht = _semver_tuple(bv), _semver_tuple(hv)
    return bool(bt and ht and bt[0] != ht[0])


def _version_in_range(version: str, rng: str):
    """Evaluate a GHSA normalized vulnerableVersionRange against a version."""
    v = _vkey(version)
    if v is None:
        return None
    for part in rng.split(","):
        part = part.strip()
        m = re.match(r"(>=|<=|<|>|=)\s*([0-9][0-9A-Za-z.\-+]*)", part)
        if not m:
            return None
        op, ver = m.group(1), m.group(2)
        t = _vkey(ver)
        if t is None:
            return None
        c = _cmp(v, t)
        if op == "<" and not (c < 0):
            return False
        if op == "<=" and not (c <= 0):
            return False
        if op == ">" and not (c > 0):
            return False
        if op == ">=" and not (c >= 0):
            return False
        if op == "=" and not (c == 0):
            return False
    return True


# --------------------------------------------------------------------------
# OSV
# --------------------------------------------------------------------------
_OSV_CACHE: dict = {}
_OSV_LOCK = threading.Lock()


def _osv_query_all(points, st: config.Settings):
    """
    Query OSV for each (eco, name, version). Returns (results, failed) where
    results = {point: [vuln records]} for points that were successfully queried
    and failed = {point} for points whose query errored (network/rate limit).
    A point in `failed` is UNOBSERVED — the caller must NOT treat its absence
    from results as 'clean', or a transient failure would be scored as 0 vulns.
    """
    results: dict = {}
    failed: set = set()
    for point in points:
        # the same (ecosystem, name, version) recurs across PRs; successful answers are kept for
        # the run, failures are not, so a transient error is retried by the next PR
        with _OSV_LOCK:
            recs = _OSV_CACHE.get(point)
        if recs is None:
            recs = _osv_query_one(*point, st)
            if recs is not None:
                with _OSV_LOCK:
                    _OSV_CACHE[point] = recs
        if recs is None:
            failed.add(point)                   # unobserved, NOT clean
        else:
            results[point] = recs
    return results, failed


def _osv_query_one(eco: str, name: str, ver: str, st: config.Settings):
    """OSV vulns for one (eco, name, version). [] = queried, none found; None =
    the query failed (network error / rate limit / bad response) and the point
    is unobserved. Retries transient 429/5xx with bounded backoff so OSV rate
    limiting doesn't silently drop points."""
    body = json.dumps({"package": {"ecosystem": eco, "name": name},
                       "version": ver}).encode()
    for attempt in range(4):
        req = urllib.request.Request(OSV_QUERY, data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=st.request_timeout) as resp:
                data = json.loads(resp.read() or "{}")
            if data.get("next_page_token"):
                return None                      # a paged answer: more vulns than this reply holds
            return data.get("vulns", []) or []
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                retry_after = e.headers.get("Retry-After")
                wait = int(retry_after) if (retry_after and retry_after.isdigit()) \
                    else (2 ** attempt)
                time.sleep(min(wait, 30))
                continue
            return None                          # 4xx (other) or exhausted -> unobserved
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError):
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            return None


def _has_fix(vuln: dict) -> bool:
    for aff in vuln.get("affected", []):
        for rng in aff.get("ranges", []):
            for ev in rng.get("events", []):
                if "fixed" in ev:
                    return True
    return False


def _severity_rank(vuln: dict) -> int:
    """Qualitative severity from a GHSA-sourced OSV record (0 if unknown)."""
    ds = vuln.get("database_specific") or {}
    sev = str(ds.get("severity", "")).upper()
    if sev in _SEVERITY_RANK:
        return _SEVERITY_RANK[sev]
    # some records only carry a CVSS vector; fall back to a coarse map if a
    # numeric base score is present in a severity entry
    for s in vuln.get("severity", []) or []:
        score = str(s.get("score", ""))
        m = re.search(r"\b(\d(?:\.\d)?)\b", score)
        if m:
            val = float(m.group(1))
            if val >= 9:
                return 4
            if val >= 7:
                return 3
            if val >= 4:
                return 2
            if val > 0:
                return 1
    return 0


def _is_malware(vuln: dict) -> bool:
    vid = str(vuln.get("id", "")).upper()
    if vid.startswith("MAL"):
        return True
    for alias in vuln.get("aliases", []) or []:
        if str(alias).upper().startswith("MAL"):
            return True
    ds = vuln.get("database_specific") or {}
    return str(ds.get("type", "")).lower() == "malware"


# --------------------------------------------------------------------------
# GHSA (GraphQL) — used only for the OSV-vs-GHSA disagreement signal
# --------------------------------------------------------------------------
def _osv_ghsa_disagreement(head_points, vulns_at, st: config.Settings, when=None):
    """
    For each version newly present at head, compare OSV's verdict (vulnerable or
    not) with GHSA's. Returns True if they disagree on any dep, False if they
    agree on all, None if GHSA can't be queried (no token / network) or answered
    in a way that cannot be read for some dep (a clean verdict is never assumed).
    `when` limits GHSA to advisories published by then and not withdrawn.
    """
    if not st.github_token or not head_points:
        return None
    disagreed = False
    for (eco, name), ver in head_points:
        ghsa = _ghsa_vulnerable(eco, name, ver, st, when)
        if ghsa is None:
            return None                          # one unobserved dep: the whole signal is unobserved
        osv_vuln = bool(vulns_at.get((eco, name, ver)))
        if ghsa != osv_vuln:
            disagreed = True
    return disagreed


def _ghsa_vulnerable(eco: str, name: str, version: str, st: config.Settings, when=None):
    ghsa_eco = _GHSA_ECO.get(eco)
    if not ghsa_eco:
        return None
    query = (
        "query($eco:SecurityAdvisoryEcosystem!,$pkg:String!){"
        "securityVulnerabilities(ecosystem:$eco,package:$pkg,first:100){"
        "pageInfo{hasNextPage} nodes{"
        "vulnerableVersionRange advisory{classification publishedAt withdrawnAt}}}}"
    )
    body = json.dumps({"query": query,
                       "variables": {"eco": ghsa_eco, "pkg": name}}).encode()
    req = urllib.request.Request(
        GITHUB_GRAPHQL, data=body,
        headers={"Authorization": f"Bearer {st.github_token}",
                 "Content-Type": "application/json",
                 "User-Agent": "riffle-repo-data-extractor"},
    )
    try:
        with urllib.request.urlopen(req, timeout=st.request_timeout) as resp:
            data = json.loads(resp.read() or "{}")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("errors"):
        return None                              # an error reply says nothing about the package
    sv = (data.get("data") or {}).get("securityVulnerabilities")
    if not isinstance(sv, dict):
        return None
    if (sv.get("pageInfo") or {}).get("hasNextPage"):
        return None                              # more than 100 advisories: the rest are unread
    vulnerable = False
    for node in sv.get("nodes") or []:
        adv = node.get("advisory") or {}
        if when is not None:
            if adv.get("withdrawnAt"):
                continue
            pub = _to_dt(adv.get("publishedAt"))
            if pub is None:
                return None
            if pub > when:
                continue
        hit = _version_in_range(version, node.get("vulnerableVersionRange") or "")
        if hit is None:
            return None                          # an unreadable range is not a negative
        vulnerable = vulnerable or hit
    return vulnerable
