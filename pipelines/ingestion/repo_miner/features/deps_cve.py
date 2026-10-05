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
import time
import urllib.request
import urllib.error

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

_EMPTY = {
    "n_deps_added": 0, "n_deps_upgraded": 0, "n_deps_downgraded": 0,
    "n_major_bumps": 0, "osv_vulns_introduced": None, "osv_vulns_fixed": None,
    "ghsa_max_severity_introduced": None, "ghsa_malware_dep": None,
    "vuln_has_fix_available": None, "osv_ghsa_disagreement": None,
}


def compute(repo: str, base: str, head: str, changed: list[str],
            st: config.Settings) -> dict:
    manifests = [p for p in changed
                 if config.path_flags(p)["touches_dep_manifest"]
                 or config.path_flags(p)["touches_lockfile"]]
    if not manifests:
        return dict(_EMPTY)

    # --- categorize every dependency change ---------------------------------
    # each maps (eco, name) -> (base_ver | None, head_ver | None)
    added, removed, upgraded, downgraded = {}, {}, {}, {}
    for m in manifests:
        eco = _ecosystem_of(m)
        if not eco:
            continue
        b = _parse_manifest(gitio.file_content_at(repo, base, m) or "", eco)
        h = _parse_manifest(gitio.file_content_at(repo, head, m) or "", eco)
        for name, hv in h.items():
            if name not in b:
                added[(eco, name)] = (None, hv)
            elif b[name] != hv:
                c = _cmp(_semver_tuple(hv), _semver_tuple(b[name]))
                if c > 0:
                    upgraded[(eco, name)] = (b[name], hv)
                elif c < 0:
                    downgraded[(eco, name)] = (b[name], hv)
        for name, bv in b.items():
            if name not in h:
                removed[(eco, name)] = (bv, None)

    result = dict(_EMPTY)
    result["n_deps_added"] = len(added)
    result["n_deps_upgraded"] = len(upgraded)
    result["n_deps_downgraded"] = len(downgraded)
    result["n_major_bumps"] = sum(
        1 for (_, _), (bv, hv) in upgraded.items() if _major_changed(bv, hv))

    if not st.enable_deps_cve:
        return result

    # --- CVE diff via OSV ---------------------------------------------------
    # every dep whose version set changed, with its before/after versions
    changes = {**added, **removed, **upgraded, **downgraded}
    # collect the concrete (eco, name, version) points we must query
    to_query = set()
    for (eco, name), (bv, hv) in changes.items():
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

    introduced_ids, fixed_ids = set(), set()
    introduced_records = []
    for (eco, name), (bv, hv) in changes.items():
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
    result["vuln_has_fix_available"] = (
        any(_has_fix(v) for v in introduced_records) if introduced_records else False)

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
                   for (eco, name), (bv, hv) in {**added, **upgraded}.items() if hv]
    result["osv_ghsa_disagreement"] = _osv_ghsa_disagreement(head_points, vulns_at, st)
    return result


# --------------------------------------------------------------------------
# manifest parsing (best-effort, per ecosystem)
# --------------------------------------------------------------------------
def _parse_manifest(text: str, eco: str) -> dict[str, str]:
    deps: dict[str, str] = {}
    if eco == "npm":
        try:
            data = json.loads(text)
            for sect in ("dependencies", "devDependencies"):
                for name, ver in (data.get(sect) or {}).items():
                    deps[name] = str(ver).lstrip("^~>=< ")
        except json.JSONDecodeError:
            pass
    elif eco == "PyPI":
        for line in text.splitlines():
            m = re.match(r"^\s*([A-Za-z0-9_.\-]+)\s*==\s*([0-9][^\s;]*)", line)
            if m:
                deps[m.group(1).lower()] = m.group(2)
    elif eco == "Go":
        for line in text.splitlines():
            m = re.match(r"^\s*([^\s]+)\s+v([0-9][^\s]*)", line)
            if m:
                deps[m.group(1)] = m.group(2)
    elif eco == "Maven":
        deps.update(_parse_pom(text))
    elif eco == "RubyGems":
        deps.update(_parse_ruby(text))
    elif eco == "crates.io":
        deps.update(_parse_cargo(text))
    elif eco == "Packagist":
        deps.update(_parse_composer(text))
    elif eco == "NuGet":
        deps.update(_parse_csproj(text))
    elif eco == "Pub":
        deps.update(_parse_pubspec(text))
    elif eco == "Hex":
        deps.update(_parse_mix(text))
    # other ecosystems: leave empty (still counts file change, just no dep diff)
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
                deps[nm.group(1)] = _clean_ver(vr.group(1))
    return deps


def _parse_composer(text: str) -> dict[str, str]:
    """composer.json require / require-dev ({"vendor/pkg": "^1.2"})."""
    deps: dict[str, str] = {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return deps
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


def _parse_pom(text: str) -> dict[str, str]:
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
        return {}

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


def _cmp(a, b) -> int:
    if a is None or b is None:
        return 0
    return (a > b) - (a < b)


def _major_changed(bv: str, hv: str) -> bool:
    bt, ht = _semver_tuple(bv), _semver_tuple(hv)
    return bool(bt and ht and bt[0] != ht[0])


def _version_in_range(version: str, rng: str):
    """Evaluate a GHSA normalized vulnerableVersionRange against a version."""
    v = _semver_tuple(version)
    if v is None:
        return None
    for part in rng.split(","):
        part = part.strip()
        m = re.match(r"(>=|<=|<|>|=)\s*([0-9][0-9A-Za-z.\-+]*)", part)
        if not m:
            return None
        op, ver = m.group(1), m.group(2)
        t = _semver_tuple(ver)
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
    for (eco, name, ver) in points:
        recs = _osv_query_one(eco, name, ver, st)
        if recs is None:
            failed.add((eco, name, ver))        # unobserved, NOT clean
        else:
            results[(eco, name, ver)] = recs
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
def _osv_ghsa_disagreement(head_points, vulns_at, st: config.Settings):
    """
    For each version newly present at head, compare OSV's verdict (vulnerable or
    not) with GHSA's. Returns True if they disagree on any dep, False if they
    agree on all, None if GHSA can't be queried (no token / network).
    """
    if not st.github_token or not head_points:
        return None
    disagreed = False
    saw_any = False
    for (eco, name), ver in head_points:
        ghsa = _ghsa_vulnerable(eco, name, ver, st)
        if ghsa is None:
            continue
        saw_any = True
        osv_vuln = bool(vulns_at.get((eco, name, ver)))
        if ghsa != osv_vuln:
            disagreed = True
    if not saw_any:
        return None
    return disagreed


def _ghsa_vulnerable(eco: str, name: str, version: str, st: config.Settings):
    ghsa_eco = _GHSA_ECO.get(eco)
    if not ghsa_eco:
        return None
    query = (
        "query($eco:SecurityAdvisoryEcosystem!,$pkg:String!){"
        "securityVulnerabilities(ecosystem:$eco,package:$pkg,first:100){nodes{"
        "vulnerableVersionRange advisory{classification}}}}"
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
    nodes = (((data.get("data") or {}).get("securityVulnerabilities") or {})
             .get("nodes") or [])
    for node in nodes:
        rng = node.get("vulnerableVersionRange") or ""
        if _version_in_range(version, rng):
            return True
    return False
