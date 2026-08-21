"""The codoc CLI — core commands (init/watch/status/sync/realize) + plumbing."""
from __future__ import annotations

from typer.testing import CliRunner

from codoc.cli.main import app
from codoc.model.event import Event, NodeOp, NodeOpKind
from codoc.model.feature import Feature
from codoc.model.voice import LessonAxis, LessonStatus, StyleLesson
from codoc.store.db import open_store

runner = CliRunner()


def test_help_lists_four_commands():
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0
    for cmd in ("init", "watch", "status", "sync"):
        assert cmd in r.output


def test_status_reports_features_and_pending(tmp_path):
    cd = tmp_path / ".codoc"
    cd.mkdir()
    s = open_store(cd)
    s.upsert_feature(Feature(title="Thing one"))
    s.append_event(Event(source="loop_a", applied=False,
                         op=NodeOp(kind=NodeOpKind.ADD_NODE, title="Proposed two")))
    s.close()

    r = runner.invoke(app, ["status", "--root", str(tmp_path)])
    assert r.exit_code == 0
    assert "1 features" in r.output
    assert "1 pending" in r.output
    assert "Proposed two" in r.output


def test_each_command_has_help():
    for cmd in ("init", "watch", "status", "sync", "realize", "propose", "install-hooks"):
        r = runner.invoke(app, [cmd, "--help"])
        assert r.exit_code == 0, f"{cmd} --help failed: {r.output}"


def test_realize_rejects_unknown_engine(tmp_path):
    cd = tmp_path / ".codoc"
    cd.mkdir()
    (cd / "realize.md").write_text('### 1. STEER FEATURE: "x"\n  do it\n')  # past the queue check
    r = runner.invoke(app, ["realize", "--root", str(tmp_path), "--engine", "bogus"])
    assert r.exit_code == 2


def test_realize_with_no_queue_exits_clean(tmp_path):
    cd = tmp_path / ".codoc"
    cd.mkdir()
    r = runner.invoke(app, ["realize", "--root", str(tmp_path)])
    assert r.exit_code == 0
    assert "Nothing queued" in r.output


def test_realize_flushes_held_drafts(tmp_path):
    """Held-draft model: `codoc realize` IS the CLI hand-off gesture. With a held draft
    in the manifest and no realize.md, it appends the hand-off signal and runs a Loop B
    pass that (re)builds realize.md — so the flush produces the agent trigger even though
    the (absent) claude CLI then exits non-zero."""
    from codoc.codoc_file.render import write_tree
    from codoc.loop import edits as edits_channel
    from codoc.loop.loop_b import realize_path
    from codoc.model.binding import Binding

    cd = tmp_path / ".codoc"; cd.mkdir()
    s = open_store(str(cd))
    f = Feature(title="Cache", description="Caches values.")
    s.upsert_feature(f)
    s.upsert_binding(Binding(feature_id=f.id, file="c.py", symbol_path="c.py::C", fingerprint="h"))
    write_tree(s, str(cd))
    s.close()
    # A held draft (handed_off=False) sitting in the manifest, no realize.md.
    edits_channel.write_manifest(str(cd), [edits_channel.Directive(
        id="d-held1", feature_id=f.id, kind="amend",
        text='UPDATE FEATURE: "Cache"\n  New intent: …', handed_off=False)])
    assert not realize_path(str(cd)).exists()

    r = runner.invoke(app, ["realize", "--root", str(tmp_path), "--engine", "cli"])
    # The flush ran before the engine: realize.md now exists (the held draft was handed off).
    assert realize_path(str(cd)).exists()
    assert "d-held1" in realize_path(str(cd)).read_text()
    # (exit code reflects the absent claude CLI / sdk — not our concern here.)


def test_help_lists_new_commands():
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0
    assert "propose" in r.output
    assert "install-hooks" in r.output


def test_propose_creates_pending_event(tmp_path):
    """``codoc propose add_node`` should create a plan proposal."""
    cd = tmp_path / ".codoc"
    cd.mkdir()
    # Seed an empty store + rendered tree.
    from codoc.codoc_file.render import write_tree
    s = open_store(cd)
    write_tree(s, str(cd))
    s.close()

    r = runner.invoke(app, [
        "propose", "add_node",
        "--root", str(tmp_path),
        "--title", "Date formatting",
        "--description", "ISO-8601 helpers.",
    ])
    assert r.exit_code == 0, r.output
    assert "Proposal created" in r.output

    s2 = open_store(cd)
    pending = s2.pending_events()
    s2.close()
    assert len(pending) == 1
    assert pending[0].op.title == "Date formatting"


def test_propose_carries_the_sibling_anchors(tmp_path):
    """`--after` / `--before` make ordering sayable from the shell, for agents and tests
    that have no IDE. Without them every proposed add/move appended, so an agent could not
    place a node where a reader expects it."""
    cd = tmp_path / ".codoc"
    cd.mkdir()
    from codoc.codoc_file.render import write_tree
    from codoc.model.feature import Feature

    with open_store(cd) as s:
        # Rank each AFTER the previous one is stored — rank_for_append reads the store,
        # so computing both first would hand them the same key.
        first = Feature(title="Alpha", rank=s.rank_for_append(None))
        s.upsert_feature(first)
        last = Feature(title="Gamma", rank=s.rank_for_append(None))
        s.upsert_feature(last)
        write_tree(s, str(cd))

    r = runner.invoke(app, [
        "propose", "add_node",
        "--root", str(tmp_path),
        "--title", "Beta",
        "--description", "in between.",
        "--after", first.id,
        "--before", last.id,
    ])
    assert r.exit_code == 0, r.output

    with open_store(cd) as s:
        op = s.pending_events()[0].op
        assert (op.after_id, op.before_id) == (first.id, last.id)
        # …and the anchors resolve to a rank between the two on accept.
        from codoc.loop.apply import apply_op
        apply_op(op, s, source="user", applied=True)
        assert [f.title for f in s.children(None)] == ["Alpha", "Beta", "Gamma"]


def test_propose_invalid_kind_exits_nonzero(tmp_path):
    cd = tmp_path / ".codoc"
    cd.mkdir()
    from codoc.codoc_file.render import write_tree
    s = open_store(cd)
    write_tree(s, str(cd))
    s.close()

    r = runner.invoke(app, [
        "propose", "bad_kind",
        "--root", str(tmp_path),
        "--title", "X",
    ])
    assert r.exit_code != 0


def test_accept_applies_proposal_from_cli(tmp_path):
    """``codoc accept`` drains the verdict and applies a code-drift proposal
    (no IDE, no agent spawn for an already-bound/descriptive ADD)."""
    cd = tmp_path / ".codoc"
    cd.mkdir()
    from codoc.codoc_file.render import write_tree
    s = open_store(cd)
    e = Event(source="loop_a", applied=False,
              op=NodeOp(kind=NodeOpKind.ADD_NODE, title="Widget",
                        description="A small UI widget.",
                        bindings=[("ui.py", "ui.py::Widget")]))
    s.append_event(e)
    write_tree(s, str(cd))
    s.close()

    r = runner.invoke(app, ["accept", e.id, "--root", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert "accepted" in r.output

    s2 = open_store(cd)
    try:
        assert s2.pending_events() == []
        assert any(f.title == "Widget" for f in s2.list_features())
    finally:
        s2.close()


def test_reject_drops_proposal_from_cli(tmp_path):
    cd = tmp_path / ".codoc"
    cd.mkdir()
    from codoc.codoc_file.render import write_tree
    s = open_store(cd)
    e = Event(source="loop_a", applied=False,
              op=NodeOp(kind=NodeOpKind.ADD_NODE, title="Doomed", description="x"))
    s.append_event(e)
    write_tree(s, str(cd))
    s.close()

    r = runner.invoke(app, ["reject", e.id, "--root", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert "rejected" in r.output

    s2 = open_store(cd)
    try:
        assert s2.pending_events() == []
        assert not any(f.title == "Doomed" for f in s2.list_features())
    finally:
        s2.close()


def test_accept_unknown_event_exits_nonzero(tmp_path):
    cd = tmp_path / ".codoc"
    cd.mkdir()
    from codoc.codoc_file.render import write_tree
    s = open_store(cd)
    write_tree(s, str(cd))
    s.close()
    r = runner.invoke(app, ["accept", "e-deadbeef", "--root", str(tmp_path)])
    assert r.exit_code != 0


def test_install_hooks_writes_settings_json(tmp_path):
    """install-hooks command should write .claude/settings.json."""
    r = runner.invoke(app, ["install-hooks", "--root", str(tmp_path)])
    assert r.exit_code == 0, r.output

    settings_path = tmp_path / ".claude" / "settings.json"
    assert settings_path.exists(), "settings.json not created"

    import json
    settings = json.loads(settings_path.read_text())
    hooks = settings.get("hooks", {})
    assert "SessionStart" in hooks
    assert "Stop" in hooks
    assert "PreToolUse" in hooks
    assert "PostToolUse" in hooks


def test_install_hooks_is_idempotent(tmp_path):
    """Running install-hooks twice should not duplicate hook entries."""
    runner.invoke(app, ["install-hooks", "--root", str(tmp_path)])
    runner.invoke(app, ["install-hooks", "--root", str(tmp_path)])

    import json
    settings_path = tmp_path / ".claude" / "settings.json"
    settings = json.loads(settings_path.read_text())
    hooks = settings.get("hooks", {})
    # Each event should have exactly ONE entry.
    for event_name in ("SessionStart", "Stop"):
        assert len(hooks[event_name]) == 1, \
            f"{event_name} has {len(hooks[event_name])} entries (expected 1)"


def test_status_before_init_is_friendly_not_a_traceback(tmp_path):
    # No .codoc/codoc.db → status must guide, not dump a sqlite traceback.
    r = runner.invoke(app, ["status", "--root", str(tmp_path)])
    assert r.exit_code == 1
    assert "codoc init" in r.output


def test_init_refuses_to_clobber_an_existing_workspace(tmp_path):
    cd = tmp_path / ".codoc"; cd.mkdir()
    s = open_store(cd); s.upsert_feature(Feature(title="Existing")); s.close()
    # Without --force, init must refuse rather than re-bootstrap fresh ids on top.
    r = runner.invoke(app, ["init", "--root", str(tmp_path), "--no-hooks"])
    assert r.exit_code == 1
    assert "already exists" in r.output


def test_version_flag_reports_a_version():
    r = runner.invoke(app, ["--version"])
    assert r.exit_code == 0
    assert "codoc" in r.output


def test_init_writes_a_codoc_gitignore(tmp_path):
    from codoc.loop.bootstrap import _write_codoc_gitignore
    cd = tmp_path / ".codoc"; cd.mkdir()
    _write_codoc_gitignore(str(cd))
    gi = (cd / ".gitignore").read_text()
    assert "tree.codoc" in gi and "!tree.codoc" in gi
    assert gi.strip().startswith("#")  # explanatory header
    # Idempotent: a second call never overwrites a user's customization.
    (cd / ".gitignore").write_text("custom\n")
    _write_codoc_gitignore(str(cd))
    assert (cd / ".gitignore").read_text() == "custom\n"


def test_serve_refuses_public_tunnel_without_optin(tmp_path):
    # A workspace must exist (serve supervises the daemon); then --tunnel without the
    # explicit opt-in must be refused, since the hub has no auth wired.
    cd = tmp_path / ".codoc"; cd.mkdir()
    s = open_store(cd); s.upsert_feature(Feature(title="X")); s.close()
    r = runner.invoke(app, ["serve", "--root", str(tmp_path), "--tunnel"])
    assert r.exit_code == 1
    assert "Refusing to expose" in r.output


def test_serve_refuses_non_localhost_host_without_optin(tmp_path):
    cd = tmp_path / ".codoc"; cd.mkdir()
    s = open_store(cd); s.upsert_feature(Feature(title="X")); s.close()
    r = runner.invoke(app, ["serve", "--root", str(tmp_path), "--host", "0.0.0.0"])
    assert r.exit_code == 1
    assert "Refusing to expose" in r.output

class TestVoiceCommand:
    """`codoc voice` — the channel that makes a learned preference correctable.

    Codoc keeps what it learns as English sentences rather than as tuned weights
    specifically so a wrong one can be read and deleted, so these tests treat the
    listing and the `forget` path as the feature, not as reporting around it.
    """

    @staticmethod
    def _workspace(tmp_path, *lessons):
        cd = tmp_path / ".codoc"
        cd.mkdir()
        s = open_store(cd)
        for lesson in lessons:
            s.upsert_lesson(lesson)
        s.close()
        return tmp_path

    @staticmethod
    def _lesson(instruction, *, status=LessonStatus.ACTIVE, evidence=2,
                axis=LessonAxis.STRUCTURE):
        return StyleLesson(axis=axis, instruction=instruction, status=status,
                           evidence=evidence, example_before="It calls the writer.",
                           example_after="Readers need one place to look.")

    def test_says_so_plainly_when_nothing_is_learned(self, tmp_path):
        root = self._workspace(tmp_path)
        r = runner.invoke(app, ["voice", "--root", str(root)])
        assert r.exit_code == 0
        assert "has not learned anything" in r.output

    def test_lists_an_active_lesson_with_its_id(self, tmp_path):
        lesson = self._lesson("Open on the caller's problem.")
        root = self._workspace(tmp_path, lesson)
        r = runner.invoke(app, ["voice", "--root", str(root)])
        assert r.exit_code == 0
        assert "Open on the caller's problem." in r.output
        assert lesson.id in r.output
        assert "applying now" in r.output

    def test_separates_what_is_applying_from_what_is_only_seen_once(self, tmp_path):
        """Collapsing the two would misreport what codoc is actually doing."""
        root = self._workspace(
            tmp_path,
            self._lesson("Applied rule."),
            self._lesson("Unconfirmed rule.", status=LessonStatus.PROVISIONAL,
                         evidence=1, axis=LessonAxis.LENGTH),
        )
        r = runner.invoke(app, ["voice", "--root", str(root)])
        assert r.exit_code == 0
        applying = r.output.index("applying now")
        waiting = r.output.index("seen once")
        assert applying < r.output.index("Applied rule.") < waiting
        assert waiting < r.output.index("Unconfirmed rule.")

    def test_forget_stops_a_lesson_applying(self, tmp_path):
        lesson = self._lesson("Wrong rule.")
        root = self._workspace(tmp_path, lesson)
        r = runner.invoke(app, ["voice", "forget", lesson.id, "--root", str(root)])
        assert r.exit_code == 0
        assert "forgotten" in r.output
        with open_store(tmp_path / ".codoc") as store:
            assert store.injectable_lessons() == []
            assert store.get_lesson(lesson.id).status is LessonStatus.RETIRED

    def test_a_forgotten_lesson_is_hidden_unless_asked_for(self, tmp_path):
        lesson = self._lesson("Wrong rule.", status=LessonStatus.RETIRED)
        root = self._workspace(tmp_path, lesson)
        assert "Wrong rule." not in runner.invoke(
            app, ["voice", "--root", str(root)]).output
        assert "Wrong rule." in runner.invoke(
            app, ["voice", "--root", str(root), "--all"]).output

    def test_keep_applies_a_lesson_seen_only_once(self, tmp_path):
        lesson = self._lesson("Good rule.", status=LessonStatus.PROVISIONAL, evidence=1)
        root = self._workspace(tmp_path, lesson)
        r = runner.invoke(app, ["voice", "keep", lesson.id, "--root", str(root)])
        assert r.exit_code == 0
        with open_store(tmp_path / ".codoc") as store:
            assert [x.id for x in store.injectable_lessons()] == [lesson.id]

    def test_why_shows_the_edit_the_lesson_came_from(self, tmp_path):
        """A preference nobody can trace back to its edit is deletable but not
        correctable, which is a worse channel than it looks."""
        lesson = self._lesson("Open on the caller's problem.")
        lesson.scope_path = ["Codoc", "The two loops"]
        lesson.source_events = ["e-deadbeef"]
        root = self._workspace(tmp_path, lesson)
        r = runner.invoke(app, ["voice", "why", lesson.id, "--root", str(root)])
        assert r.exit_code == 0
        assert "It calls the writer." in r.output
        assert "Readers need one place to look." in r.output
        assert "Codoc / The two loops" in r.output
        assert "e-deadbeef" in r.output

    def test_an_unknown_lesson_id_fails_loudly(self, tmp_path):
        root = self._workspace(tmp_path)
        r = runner.invoke(app, ["voice", "forget", "v-nope", "--root", str(root)])
        assert r.exit_code == 1
        assert "no lesson" in r.output

    def test_an_unknown_action_fails_rather_than_listing(self, tmp_path):
        root = self._workspace(tmp_path, self._lesson("A rule."))
        r = runner.invoke(app, ["voice", "banana", "--root", str(root)])
        assert r.exit_code == 1
        assert "unknown action" in r.output



# -- the prose gate's line in `codoc status` --------------------------------------


def test_status_reports_what_the_prose_gate_has_been_finding(tmp_path):
    from codoc.loop import prose

    cd = tmp_path / ".codoc"; cd.mkdir()
    s = open_store(cd)
    s.upsert_feature(Feature(title="Existing", description="x"))
    prose.record(s, checked=1)
    prose.record(s, checked=1, defects=prose.check(
        None, "Ensures a robust sync.", names=("a.py b",)))
    s.close()

    r = runner.invoke(app, ["status", "--root", str(tmp_path)])
    assert r.exit_code == 0
    assert "prose gate" in r.output and "1/2" in r.output
    assert "machine-register" in r.output


def test_status_says_nothing_about_a_gate_that_has_checked_nothing(tmp_path):
    # A fresh workspace has no rate, and printing "nothing checked yet" on the
    # line under the headline would make an absence look like a finding.
    cd = tmp_path / ".codoc"; cd.mkdir()
    s = open_store(cd); s.upsert_feature(Feature(title="Existing")); s.close()

    r = runner.invoke(app, ["status", "--root", str(tmp_path)])
    assert r.exit_code == 0
    assert "prose gate" not in r.output
