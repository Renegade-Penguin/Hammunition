# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Third-party apt repositories against a pinned key.  D-040.

The contract, in the order the engine keeps it:

* the key is fetched and its computed fingerprint must equal the manifest's
  before anything is kept -- a wrong key is discarded, not installed;
* the repository is added only when the archive as configured has no
  candidate for the unit's own packages (D-022), and never over a file that
  is not this engine's work;
* each repository has its own consent gate that ``--yes`` cannot satisfy
  and whose environment answer is the fingerprint itself;
* the key fetch runs before any command, the two files land next, then
  ``apt-get update`` and a simulate, then the apt step;
* ``uninstall`` removes exactly the files the log attributes and refreshes
  apt afterwards.

Every test here plans against a recording runner and a fake transport; the
suite's conftest refuses the real network and the real apt.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from typing import IO, Any

import pytest
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from hammunition.backends import (  # noqa: E402
    Action,
    AptBackend,
    BackendError,
    Command,
    RecordingRunner,
)
from hammunition.backends.apt_repo import (  # noqa: E402
    AptRepoBackend,
    RepoState,
    render_sources,
)
from hammunition.consent import (  # noqa: E402
    ConsentDeclined,
    ConsentUnavailable,
    Decision,
    repo_env_var,
    resolve_repo_consent,
)
from hammunition.execute import artifact_removal_steps, commands_for  # noqa: E402
from hammunition.manifest.schema import AptRepo, ManifestError  # noqa: E402
from hammunition.plan import PlanError, resolve  # noqa: E402
from hammunition.state import RemovalPaths, TransactionLog, plan_removal  # noqa: E402
from hammunition.state.uninstall import files_installed_by_hammunition  # noqa: E402
from test_openpgp import FIXTURE_ASC, FIXTURE_FINGERPRINT, _binary  # noqa: E402
from test_plan import TARGET, _apt, _manifest, _profile  # noqa: E402

REPO: dict[str, Any] = {
    "name": "vendor",
    "uri": "https://example.invalid/apt",
    "suites": ["stable"],
    "components": ["main"],
    "key_url": "https://example.invalid/key.asc",
    "key_fingerprint": FIXTURE_FINGERPRINT,
    "rationale": "A fixture repository whose rationale is long enough to be shown.",
}
ENV = "HAMMUNITION_ACCEPT_APT_REPO_VENDOR"


class FakeTransport:
    def __init__(self, body: bytes = FIXTURE_ASC) -> None:
        self.body = body
        self.requested: list[str] = []

    @contextmanager
    def open(self, url: str) -> Any:
        self.requested.append(url)
        stream: IO[bytes] = BytesIO(self.body)
        yield stream


def _repo(**overrides: Any) -> AptRepo:
    return AptRepo.model_validate({**REPO, **overrides})


def _backend(tmp_path: Path, transport: FakeTransport | None = None) -> AptRepoBackend:
    return AptRepoBackend(
        cache_dir=tmp_path / "cache",
        staging_dir=tmp_path / "staging",
        sources_dir=tmp_path / "etc" / "sources.list.d",
        keyrings_dir=tmp_path / "etc" / "keyrings",
        transport=transport or FakeTransport(),
    )


def _unit(**overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "name": "editor",
        "install": [{"install": {"method": "apt", "packages": ["editor"]}}],
        "apt_repos": [REPO],
    }
    return _manifest(**{**fields, **overrides})


def _plan(tmp_path: Path, repos: AptRepoBackend | None, **kwargs: Any) -> Any:
    catalog = kwargs.pop("catalog", {"editor": _unit()})
    known = kwargs.pop("known", {})
    return resolve(
        kwargs.pop("names", ["editor"]),
        catalog=catalog,
        profiles=kwargs.pop("profiles", {}),
        target=TARGET,
        apt=_apt(tmp_path, known),
        user="operator",
        repos=repos,
        resolution_context=kwargs.pop("resolution_context", None),
    )


# ---------------------------------------------------------------------------
# Schema: a repository declaration is well-formed or refused
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value", "why"),
    [
        ("name", "../etc", "must be lower-case"),
        ("name", "Vendor", "must be lower-case"),
        ("key_fingerprint", "0" * 39, "40 \\(v4\\) or 64"),
        ("key_fingerprint", "not-hex-at-all-not-hex-at-all-not-hex-at", "40 \\(v4\\) or 64"),
        ("key_url", "http://example.invalid/key.asc", "must be https"),
        ("uri", "http://example.invalid/apt", "must be https"),
        ("suites", [], "suites and components"),
    ],
)
def test_a_malformed_repo_declaration_is_refused(field: str, value: Any, why: str) -> None:
    with pytest.raises((ValidationError, ManifestError), match=why):
        _repo(**{field: value})


def test_a_flat_repository_has_no_components_and_writes_no_components_line() -> None:
    """The openSUSE Build Service publishes flat repositories (D-040 amendment,
    2026-10-05): `Suites: ./` and no `Components:` line. An empty `Components:`
    line is a malformed stanza and apt refuses the whole file (measured in a
    Debian 13 container, 2026-10-05), so the line must be absent, not empty."""
    repo = _repo(suites=["./"], components=[])
    text = render_sources(repo, Path("/etc/apt/keyrings/vendor.gpg"), unit="editor")
    assert "Suites: ./\n" in text
    assert "Components" not in text
    assert text.endswith("Signed-By: /etc/apt/keyrings/vendor.gpg\n")


@pytest.mark.parametrize(
    ("suites", "components", "why"),
    [
        (["trixie"], [], "components may be empty only for a flat repository"),
        (["./"], ["main"], "takes no components"),
        (["trixie", "./"], [], "components may be empty only for a flat repository"),
    ],
)
def test_flat_and_dists_shapes_are_never_mixed(
    suites: list[str], components: list[str], why: str
) -> None:
    with pytest.raises((ValidationError, ManifestError), match=why):
        _repo(suites=suites, components=components)


def test_a_spaced_lower_case_fingerprint_is_accepted() -> None:
    assert _repo(key_fingerprint="527c a4ae a244 4bd2 40a9 fdcd 3852 ff00 e343 0290")


# ---------------------------------------------------------------------------
# The backend: files, state, steps
# ---------------------------------------------------------------------------


def test_the_two_files_are_named_by_the_repository(tmp_path: Path) -> None:
    files = _backend(tmp_path).files_for(_repo())
    assert files.sources.name == "vendor.sources"
    assert files.keyring.name == "vendor.gpg"


def test_the_source_file_trusts_the_key_for_this_repository_only(tmp_path: Path) -> None:
    text = render_sources(_repo(), Path("/etc/apt/keyrings/vendor.gpg"), unit="editor")
    assert "Signed-By: /etc/apt/keyrings/vendor.gpg" in text
    assert "URIs: https://example.invalid/apt\n" in text
    assert "Suites: stable\n" in text
    assert "# generated by hammunition for editor" in text
    assert "hammunition uninstall editor" in text


def test_state_is_absent_ours_or_foreign(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    repo = _repo()
    files = backend.files_for(repo)
    assert backend.state(repo, unit="editor") is RepoState.absent

    files.sources.parent.mkdir(parents=True)
    files.keyring.parent.mkdir(parents=True)
    files.sources.write_text(render_sources(repo, files.keyring, unit="editor"))
    files.keyring.write_bytes(_binary())
    assert backend.state(repo, unit="editor") is RepoState.ours

    files.sources.write_text("Types: deb\nURIs: https://elsewhere.invalid\n")
    assert backend.state(repo, unit="editor") is RepoState.foreign


def test_one_file_of_the_pair_is_foreign_not_absent(tmp_path: Path) -> None:
    """Half a repository is somebody's; adding over it would replace their
    keyring with ours."""
    backend = _backend(tmp_path)
    repo = _repo()
    files = backend.files_for(repo)
    files.keyring.parent.mkdir(parents=True)
    files.keyring.write_bytes(b"whatever")
    assert backend.state(repo, unit="editor") is RepoState.foreign


def test_our_source_file_beside_a_different_key_is_foreign(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    repo = _repo()
    files = backend.files_for(repo)
    files.sources.parent.mkdir(parents=True)
    files.keyring.parent.mkdir(parents=True)
    files.sources.write_text(render_sources(repo, files.keyring, unit="editor"))
    files.keyring.write_bytes(FIXTURE_ASC)
    other = _repo(key_fingerprint="0" * 40)
    assert backend.state(other, unit="editor") is RepoState.foreign


def test_steps_fetch_then_stage_then_install_both_files_as_root(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    steps = backend.steps(_repo(), unit="editor")
    kinds = [s.kind if isinstance(s, Action) else s.argv[:4] for s in steps]
    assert kinds == [
        "fetch",
        "stage-apt-source",
        ("install", "-D", "-m", "0644"),
        ("install", "-D", "-m", "0644"),
    ]
    installs = [s for s in steps if isinstance(s, Command)]
    assert all(s.requires_root for s in installs)
    assert installs[0].argv[5] == str(backend.files_for(_repo()).keyring)
    assert installs[1].argv[5] == str(backend.files_for(_repo()).sources)
    fetch = steps[0]
    assert isinstance(fetch, Action)
    assert FIXTURE_FINGERPRINT in fetch.detail


def test_the_fetch_keeps_the_binary_form_of_a_key_whose_fingerprint_matches(tmp_path: Path) -> None:
    """The publisher served armor; the fingerprint was computed over the
    packets inside it, and the packets are what is kept."""
    transport = FakeTransport()
    backend = _backend(tmp_path, transport)
    step = backend.steps(_repo(), unit="editor")[0]
    assert isinstance(step, Action)
    outcome = step.perform()
    assert FIXTURE_FINGERPRINT in outcome
    assert backend.cached_key(_repo()).read_bytes() == _binary()
    assert transport.requested == ["https://example.invalid/key.asc"]


def test_a_key_with_the_wrong_fingerprint_is_discarded_by_name(tmp_path: Path) -> None:
    backend = _backend(tmp_path, FakeTransport())
    wrong = _repo(key_fingerprint="0" * 40)
    step = backend.steps(wrong, unit="editor")[0]
    assert isinstance(step, Action)
    with pytest.raises(BackendError) as excinfo:
        step.perform()
    message = str(excinfo.value)
    assert "expected primary fingerprint: " + "0" * 40 in message
    assert "file carries:                 " + FIXTURE_FINGERPRINT in message
    assert not backend.cached_key(wrong).exists()
    assert (
        not list((tmp_path / "cache").glob("*.part.*")) if (tmp_path / "cache").exists() else True
    )


def test_a_file_that_is_not_a_key_is_refused_before_any_comparison(tmp_path: Path) -> None:
    backend = _backend(tmp_path, FakeTransport(b"<html>moved</html>"))
    step = backend.steps(_repo(), unit="editor")[0]
    assert isinstance(step, Action)
    with pytest.raises(BackendError, match="not a key file"):
        step.perform()


def test_a_cached_key_is_re_verified_not_trusted(tmp_path: Path) -> None:
    transport = FakeTransport()
    backend = _backend(tmp_path, transport)
    repo = _repo()
    cached = backend.cached_key(repo)
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"stale garbage")
    step = backend.steps(repo, unit="editor")[0]
    assert isinstance(step, Action)
    step.perform()
    assert cached.read_bytes() == _binary()
    assert transport.requested == ["https://example.invalid/key.asc"]


def test_a_key_over_a_megabyte_is_abandoned(tmp_path: Path) -> None:
    backend = _backend(tmp_path, FakeTransport(b"x" * (1024 * 1024 + 1)))
    step = backend.steps(_repo(), unit="editor")[0]
    assert isinstance(step, Action)
    with pytest.raises(BackendError, match="exceeds"):
        step.perform()


def test_staging_writes_the_rendered_source_privately(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    repo = _repo()
    step = backend.steps(repo, unit="editor")[1]
    assert isinstance(step, Action)
    step.perform()
    staged = backend.staged_sources(repo)
    assert staged.read_text() == render_sources(
        repo, backend.files_for(repo).keyring, unit="editor"
    )
    assert staged.stat().st_mode & 0o777 == 0o600


# ---------------------------------------------------------------------------
# Consent: one gate per repository, the fingerprint is the answer
# ---------------------------------------------------------------------------


def _consent(**kwargs: Any) -> Any:
    return resolve_repo_consent(
        _repo(),
        "editor",
        sources="/etc/apt/sources.list.d/vendor.sources",
        keyring="/etc/apt/keyrings/vendor.gpg",
        **kwargs,
    )


def test_the_env_var_is_derived_from_the_repository_name() -> None:
    assert repo_env_var(_repo()) == ENV
    assert (
        repo_env_var(_repo(name="microsoft-vscode"))
        == "HAMMUNITION_ACCEPT_APT_REPO_MICROSOFT_VSCODE"
    )


def test_the_environment_answer_is_the_fingerprint() -> None:
    record = _consent(environ={ENV: FIXTURE_FINGERPRINT.lower()}, prompt=None)
    assert record.decision is Decision.environment
    assert record.extra["kind"] == "apt_repo"
    assert record.extra["key_fingerprint"] == FIXTURE_FINGERPRINT
    assert record.profile == "apt-repo:vendor"


def test_a_bare_one_is_refused_with_the_value_it_should_have_held() -> None:
    with pytest.raises(ConsentUnavailable, match=f"{ENV}={FIXTURE_FINGERPRINT}"):
        _consent(environ={ENV: "1"}, prompt=None)


def test_a_stale_fingerprint_is_refused() -> None:
    """The point of the fingerprint-as-answer: a re-pinned key stops the
    script that affirmed the old one."""
    with pytest.raises(ConsentUnavailable):
        _consent(environ={ENV: "0" * 40}, prompt=None)


def test_yes_does_not_add_a_repository() -> None:
    with pytest.raises(ConsentUnavailable, match="--yes does not satisfy"):
        _consent(environ={}, prompt=None, assume_yes=True)


def test_the_prompt_shows_the_files_and_the_fingerprint() -> None:
    shown: list[str] = []

    def prompt(text: str) -> bool:
        shown.append(text)
        return True

    record = _consent(environ={}, prompt=prompt)
    assert record.decision is Decision.interactive
    assert "/etc/apt/sources.list.d/vendor.sources" in shown[0]
    assert "/etc/apt/keyrings/vendor.gpg" in shown[0]
    assert FIXTURE_FINGERPRINT in shown[0]
    assert record.disclosure_text == shown[0]


def test_declining_the_prompt_raises() -> None:
    with pytest.raises(ConsentDeclined):
        _consent(environ={}, prompt=lambda _text: False)


# ---------------------------------------------------------------------------
# Planning: when a repository is added, and when it is not
# ---------------------------------------------------------------------------


def test_no_backend_still_refuses_by_name(tmp_path: Path) -> None:
    with pytest.raises(PlanError) as excinfo:
        _plan(tmp_path, None)
    assert "no repository backend" in excinfo.value.blockers[0].reason


def test_a_third_party_repository_is_refused_by_name_when_offline(tmp_path: Path) -> None:
    from hammunition.resolution import ResolutionContext

    with pytest.raises(PlanError) as excinfo:
        _plan(tmp_path, _backend(tmp_path), resolution_context=ResolutionContext(offline=True))
    blocker = excinfo.value.blockers[0]
    assert blocker.subject == "editor"
    assert "offline: it adds the apt repository vendor" in blocker.reason
    online = _plan(tmp_path, _backend(tmp_path), resolution_context=ResolutionContext())
    assert [a.repo.name for a in online.apt_repos] == ["vendor"]


def test_an_absent_repository_is_added_when_apt_has_no_candidate(tmp_path: Path) -> None:
    plan = _plan(tmp_path, _backend(tmp_path))
    assert [a.repo.name for a in plan.apt_repos] == ["vendor"]
    addition = plan.apt_repos[0]
    assert addition.unit == "editor"
    assert addition.packages == ("editor",)
    assert addition.sources.endswith("vendor.sources")
    assert addition.keyring.endswith("vendor.gpg")
    assert plan.apt_to_install == ("editor",)
    assert not plan.is_empty


def test_repo_supplied_packages_leave_the_plan_time_simulate(tmp_path: Path) -> None:
    """apt cannot resolve from a repository it does not have yet; asking it
    would refuse every transaction that adds one."""
    runner = RecordingRunner()
    apt = _apt(tmp_path, {})
    apt.runner = runner
    resolve(
        ["editor"],
        catalog={"editor": _unit()},
        profiles={},
        target=TARGET,
        apt=apt,
        user="operator",
        repos=_backend(tmp_path),
    )
    simulated = [c for c in runner.commands if "--simulate" in c.argv]
    assert simulated == []


def test_a_candidate_in_the_archive_means_no_repository(tmp_path: Path) -> None:
    """D-022: Parrot ships codium itself; the manifest's repository is not
    added there, and the plan says so."""
    plan = _plan(tmp_path, _backend(tmp_path), known={"editor": None})
    assert plan.apt_repos == ()
    assert plan.apt_to_install == ("editor",)
    assert any("not added (D-022)" in note for note in plan.notes)


def test_a_foreign_file_is_a_refusal_never_an_overwrite(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    files = backend.files_for(_repo())
    files.sources.parent.mkdir(parents=True)
    files.sources.write_text("Types: deb\nURIs: https://someone-elses.invalid\n")
    with pytest.raises(PlanError) as excinfo:
        _plan(tmp_path, backend)
    blocker = excinfo.value.blockers[0]
    assert "not this engine's work" in blocker.reason
    assert "never overwritten" in (blocker.remedy or "")


def test_our_repository_with_no_candidate_points_at_refresh(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    repo = _repo()
    files = backend.files_for(repo)
    files.sources.parent.mkdir(parents=True)
    files.keyring.parent.mkdir(parents=True)
    files.sources.write_text(render_sources(repo, files.keyring, unit="editor"))
    files.keyring.write_bytes(FIXTURE_ASC)
    with pytest.raises(PlanError) as excinfo:
        _plan(tmp_path, backend)
    assert "already configured" in excinfo.value.blockers[0].reason
    assert "apt-get update" in (excinfo.value.blockers[0].remedy or "")


def test_a_missing_dependency_is_never_a_reason_to_add_a_repository(tmp_path: Path) -> None:
    catalog = {"editor": _unit(depends=["libsomething"])}
    with pytest.raises(PlanError) as excinfo:
        _plan(tmp_path, _backend(tmp_path), catalog=catalog, known={"editor": None})
    assert "libsomething" in excinfo.value.blockers[0].reason


def test_a_profile_member_needing_a_repository_is_planned_not_deferred(tmp_path: Path) -> None:
    """D-039 defers what the target does not offer; a repository the manifest
    declares is an offer, so the member is planned with its gate."""
    present = _manifest(
        name="present", install=[{"install": {"method": "apt", "packages": ["present"]}}]
    )
    catalog = {"editor": _unit(), "present": present}
    profile = _profile(name="editors", packages=["present", "editor"])
    plan = _plan(
        tmp_path,
        _backend(tmp_path),
        names=["editors"],
        catalog=catalog,
        profiles={"editors": profile},
        known={"present": None},
    )
    assert {p.name for p in plan.packages} == {"present", "editor"}
    assert plan.deferrals == ()
    assert len(plan.apt_repos) == 1


# ---------------------------------------------------------------------------
# A repository narrowed to some targets (`when`): Kismet's per-release trees
# ---------------------------------------------------------------------------


def _per_release_unit() -> Any:
    """Two trees, one per release, the shape Kismet's repository has."""
    return _unit(
        apt_repos=[
            {**REPO, "name": "vendor-trixie", "when": {"distro": ["debian"]}},
            {
                **REPO,
                "name": "vendor-noble",
                "uri": "https://example.invalid/apt/noble",
                "suites": ["noble"],
                "when": {"distro": ["ubuntu"], "distro_version": ["24.04"]},
            },
        ]
    )


def test_only_the_repository_whose_when_matches_is_added(tmp_path: Path) -> None:
    plan = _plan(tmp_path, _backend(tmp_path), catalog={"editor": _per_release_unit()})
    assert [a.repo.name for a in plan.apt_repos] == ["vendor-trixie"]


def test_a_target_no_repository_applies_to_is_not_offered_the_unit(tmp_path: Path) -> None:
    """Named: the unit is not in the archive and no repository serves this
    target, so the ordinary refusal applies -- never a repository for
    another release."""
    unit = _unit(apt_repos=[{**REPO, "when": {"distro": ["ubuntu"]}}])
    with pytest.raises(PlanError) as excinfo:
        _plan(tmp_path, _backend(tmp_path), catalog={"editor": unit})
    reasons = " ".join(b.reason for b in excinfo.value.blockers)
    assert "editor" in reasons
    assert "no repository backend" not in reasons


def test_a_repository_for_another_target_needs_no_backend(tmp_path: Path) -> None:
    """The no-backend refusal names repositories this target would add, and
    here there are none; a candidate in the archive plans cleanly."""
    unit = _unit(apt_repos=[{**REPO, "when": {"distro": ["ubuntu"]}}])
    plan = _plan(tmp_path, None, catalog={"editor": unit}, known={"editor": None})
    assert plan.apt_repos == ()
    assert plan.apt_to_install == ("editor",)


def test_a_profile_member_no_repository_serves_is_deferred(tmp_path: Path) -> None:
    """D-039: the member the target cannot get is deferred by name."""
    present = _manifest(
        name="present", install=[{"install": {"method": "apt", "packages": ["present"]}}]
    )
    unit = _unit(apt_repos=[{**REPO, "when": {"distro": ["ubuntu"]}}])
    plan = _plan(
        tmp_path,
        _backend(tmp_path),
        names=["editors"],
        catalog={"editor": unit, "present": present},
        profiles={"editors": _profile(name="editors", packages=["present", "editor"])},
        known={"present": None},
    )
    assert [d.subject for d in plan.deferrals] == ["editor"]
    assert plan.apt_repos == ()


def test_two_repositories_of_one_name_are_refused() -> None:
    with pytest.raises((ManifestError, ValidationError), match="duplicate apt repo names"):
        _unit(apt_repos=[REPO, {**REPO, "when": {"distro": ["ubuntu"]}}])


# ---------------------------------------------------------------------------
# Execution order
# ---------------------------------------------------------------------------


def test_the_key_fetch_runs_first_then_files_update_simulate_install(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    plan = _plan(tmp_path, backend)
    apt = AptBackend(RecordingRunner())
    steps = commands_for(plan, apt, repos=backend)
    shape = [s.kind if isinstance(s, Action) else s.argv[:3] for s in steps]
    assert (
        shape
        == [
            "fetch",
            "stage-apt-source",
            ("install", "-D", "-m"),
            ("install", "-D", "-m"),
            ("apt-get", "-o", "Acquire::Retries=3"),
            ("apt-get", "-o", "Acquire::Retries=3"),
            ("apt-get", "-o", "Acquire::Retries=3"),
        ][: len(shape)]
    )
    argvs = [s.argv for s in steps if isinstance(s, Command)]
    assert any(a[0] == "apt-get" and "update" in a for a in argvs)
    simulate = next(s for s in steps if isinstance(s, Command) and "--simulate" in s.argv)
    assert "editor" in simulate.argv
    assert "editor" in simulate.description


def test_a_plan_with_repositories_and_no_backend_is_an_error(tmp_path: Path) -> None:
    plan = _plan(tmp_path, _backend(tmp_path))
    with pytest.raises(BackendError, match="no repository backend"):
        commands_for(plan, AptBackend(RecordingRunner()))


# ---------------------------------------------------------------------------
# Uninstall: attribution from the log, both files, then apt-get update
# ---------------------------------------------------------------------------


def _log_with_installs(tmp_path: Path, *dests: str) -> TransactionLog:
    log = TransactionLog(path=tmp_path / "log.jsonl")
    for dest in dests:
        log.append(
            {
                "event": "command_end",
                "argv": ["install", "-D", "-m", "0644", "/cache/x", dest],
                "returncode": 0,
            }
        )
    return log


def test_files_installed_as_0644_are_attributed(tmp_path: Path) -> None:
    log = _log_with_installs(tmp_path, "/etc/apt/keyrings/vendor.gpg")
    assert files_installed_by_hammunition(log) == frozenset({"/etc/apt/keyrings/vendor.gpg"})


def test_uninstall_removes_both_attributed_files_and_nothing_else(tmp_path: Path) -> None:
    etc = tmp_path / "etc"
    sources = etc / "sources.list.d" / "vendor.sources"
    keyring = etc / "keyrings" / "vendor.gpg"
    foreign = etc / "sources.list.d" / "other.sources"
    for path in (sources, keyring, foreign):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    log = _log_with_installs(tmp_path, str(keyring), str(sources))
    plan = plan_removal(
        ["editor"],
        catalog={"editor": _unit()},
        profiles={},
        target=TARGET,
        attributed=frozenset({"editor"}),
        states={},
        paths=RemovalPaths(
            prefix=tmp_path / "prefix",
            venv_root=tmp_path / "venvs",
            bin_dir=tmp_path / "bin",
            applications_dir=tmp_path / "apps",
        ),
        attributed_files=files_installed_by_hammunition(log),
        log=log,
    )
    removals = plan.artifacts["editor"]
    assert {(r.kind, r.path, r.basis, r.requires_root) for r in removals} == {
        ("apt-repo", keyring, "log", True),
        ("apt-repo", sources, "log", True),
    }
    steps = artifact_removal_steps(plan)
    assert [s.argv for s in steps if isinstance(s, Command)] == [
        ("rm", "-f", "--", str(keyring)),
        ("rm", "-f", "--", str(sources)),
    ]


def test_an_unattributed_source_file_of_the_same_name_is_left_alone(tmp_path: Path) -> None:
    """The operator wrote /etc/apt/sources.list.d/vendor.sources themselves;
    it is not in the log, so uninstall does not know it and does not touch it."""
    etc = tmp_path / "etc"
    sources = etc / "sources.list.d" / "vendor.sources"
    sources.parent.mkdir(parents=True)
    sources.write_text("theirs")
    log = TransactionLog(path=tmp_path / "log.jsonl")
    plan = plan_removal(
        ["editor"],
        catalog={"editor": _unit()},
        profiles={},
        target=TARGET,
        attributed=frozenset(),
        states={},
        paths=RemovalPaths(
            prefix=tmp_path / "prefix",
            venv_root=tmp_path / "venvs",
            bin_dir=tmp_path / "bin",
            applications_dir=tmp_path / "apps",
        ),
        attributed_files=files_installed_by_hammunition(log),
        log=log,
    )
    assert plan.artifacts == {}
