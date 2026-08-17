// The participant page in another language.
//
// The failure this guards is not a clumsy sentence. It is a translation that
// changes the INSTRUMENT: an item that quietly disappears, a scale that loses an
// end label, or a set of options that no longer lines up with what gets stored.
// Any of those produce a page that looks fine and numbers that cannot be pooled
// with the English ones.
//
//   node --test test/i18n.test.js
import test from 'node:test';
import assert from 'node:assert/strict';
import {
    PRESTUDY, AFTER_CONDITION, CONSTRUCTS, MANIPULATION_CHECK,
    SIGNOFF, REFLECTION, SCENARIOS, PROJECTS, HOW_TO_START, RESPONSIBILITY,
    QUIZZES, AFTER_QUIZZES,
} from '../participant/steps.js';
import {
    LANGUAGES, DEFAULT_LANGUAGE, isLanguage, setLanguage, language, t,
    localize, localizeAll, missingKeys,
} from '../participant/i18n/index.js';

/** Every block, with the key prefix it is looked up under. */
const BLOCKS = [
    ['prestudy', PRESTUDY],
    ['after', AFTER_CONDITION],
    ['check', MANIPULATION_CHECK],
    ['signoff', SIGNOFF],
    ['reflect', REFLECTION],
    ['scenario', SCENARIOS],
];

test('English is the default, and an unknown code falls back to it', () => {
    assert.equal(setLanguage(undefined), 'en');
    assert.equal(setLanguage('kl-KL'), DEFAULT_LANGUAGE);
    assert.equal(setLanguage(''), DEFAULT_LANGUAGE);
    assert.equal(isLanguage('zh-Hans'), true);
    assert.equal(isLanguage('fr'), false);
});

test('English asks for nothing and changes nothing', () => {
    setLanguage('en');
    for (const [set, items] of BLOCKS) {
        for (const before of items) {
            const after = localize(set, before);
            assert.deepEqual(after, before, `${set}.${before.id} was altered`);
        }
    }
    assert.deepEqual(missingKeys(), [], 'English needs no table, so nothing can be missing');
});

test('every language offers a name in its own script', () => {
    // A menu that says "Chinese (Simplified)" in English to somebody who is about
    // to be run in Chinese is asking them to read the language they are leaving.
    for (const [code, info] of Object.entries(LANGUAGES)) {
        assert.ok(info.name, `${code} has no English name`);
        assert.ok(info.endonym, `${code} has no name in its own script`);
    }
    assert.equal(LANGUAGES['zh-Hans'].endonym, '简体中文');
});

test('zh-Hans translates every word a participant reads', () => {
    // The gap this catches: one item added to the instrument and not translated.
    // A participant would see a single English sentence in a Chinese page, which
    // is survivable, but a whole block would not be.
    setLanguage('zh-Hans');
    for (const [set, items] of BLOCKS) localizeAll(set, items);
    for (const c of CONSTRUCTS) t(`block.${c.id}.title`, c.title);
    for (const key of ['scenario.option.first', 'scenario.option.second', 'scenario.option.none']) {
        t(key, '');
    }
    assert.deepEqual(missingKeys(), [],
        'these keys have no zh-Hans entry, so they would render in English');
});

test('a translation can change words and nothing else', () => {
    // The load-bearing property. instrument.js is the one place that says which
    // items exist, in what order, on which scale, and which are reverse keyed. A
    // language file that could add an item or drop an option would be a second
    // instrument, and two instruments cannot be pooled.
    setLanguage('zh-Hans');
    for (const [set, items] of BLOCKS) {
        const out = localizeAll(set, items);
        assert.equal(out.length, items.length, `${set} changed length`);
        for (const [i, after] of out.entries()) {
            const before = items[i];
            assert.equal(after.id, before.id, 'ids are the join between the two');
            assert.equal(after.type, before.type, `${before.id} changed type`);
            assert.equal(after.reverse, before.reverse, `${before.id} changed keying`);
            assert.equal(after.c, before.c, `${before.id} changed block`);
            assert.equal(after.scale, before.scale, `${before.id} changed scale`);
            assert.equal((after.options || []).length, (before.options || []).length,
                `${before.id} changed how many options it offers`);
        }
    }
});

test('a scale keeps both of its end labels, or it is unreadable', () => {
    setLanguage('zh-Hans');
    for (const [set, items] of BLOCKS) {
        for (const q of localizeAll(set, items)) {
            if (q.type !== 'scale5') continue;
            assert.ok(q.low && q.high, `${set}.${q.id} lost an end label`);
            assert.notEqual(q.low, q.high, `${set}.${q.id} has the same word at both ends`);
        }
    }
});

test('what gets stored stays English, whatever is on screen', () => {
    // One analysis has to read every participant. If a Chinese session stored
    // 「总是」 where an English one stored "Always", every choice item would need
    // a per-language decoder, and the first person to forget one would silently
    // drop a condition.
    setLanguage('zh-Hans');
    const readsDiff = localize('prestudy', PRESTUDY.find((q) => q.id === 'readsDiff'));
    assert.notDeepEqual(readsDiff.options, ['Always', 'Usually', 'About half the time', 'Rarely', 'Never'],
        'the labels really are translated');
    // The English options are what `shouldExclude` and the analysis match on, and
    // they are read from the instrument rather than from the localised copy.
    const source = PRESTUDY.find((q) => q.id === 'readsDiff');
    assert.deepEqual(source.options,
        ['Always', 'Usually', 'About half the time', 'Rarely', 'Never']);
});

test('names of things are not translated', () => {
    // The code, the paths and the two projects are in English on their screen.
    // A translated file name sends somebody looking for a file that is not there.
    setLanguage('zh-Hans');
    const grounds = localize('signoff', SIGNOFF.find((q) => q.id === 'grounds'));
    assert.ok(grounds.options.some((o) => o.includes('diff')), 'diff stays diff');
    const recall = localize('reflect', REFLECTION.find((q) => q.id === 'recall'));
    assert.ok(recall.label.length > 0);
});

test('every briefing, start instruction and page string is translated', () => {
    // The templated keys — project.<name>.does.<i>.term and friends — cannot be
    // found by reading the source, so they are walked here the way the page walks
    // them. This is what catches a project gaining a bullet that nobody
    // translated, which would show as one English line inside a Chinese briefing.
    setLanguage('zh-Hans');
    for (const [name, project] of Object.entries(PROJECTS)) {
        t(`project.${name}.oneLine`, project.oneLine);
        t(`project.${name}.notScope`, project.notScope);
        project.problem.forEach((line, i) => t(`project.${name}.problem.${i}`, line));
        project.does.forEach(([w, d], i) => {
            t(`project.${name}.does.${i}.term`, w);
            t(`project.${name}.does.${i}.body`, d);
        });
        project.judgement.forEach(([w, d], i) => {
            t(`project.${name}.judgement.${i}.term`, w);
            t(`project.${name}.judgement.${i}.body`, d);
        });
    }
    for (const [condition, how] of Object.entries(HOW_TO_START)) {
        t(`start.${condition}.title`, how.title);
        how.steps.forEach(([text], i) => t(`start.${condition}.step.${i}`, text));
        how.about.forEach((line, i) => t(`start.${condition}.about.${i}`, line));
    }
    RESPONSIBILITY.forEach((line, i) => t(`responsibility.${i}`, line));

    assert.deepEqual(missingKeys(), [],
        'these would render in English inside a Chinese page');
});

test('every quiz question and every option is translated', () => {
    // Twenty-four questions and ninety-six options. One option left in English
    // inside a Chinese question is not a blemish: it is the only Latin text on
    // the screen, which makes it the option that stands out, and a distractor
    // that draws the eye for a reason unrelated to its content is a broken item.
    setLanguage('zh-Hans');
    for (const [project, questions] of Object.entries(QUIZZES)) {
        for (const q of questions) {
            t(`quiz.${project}.${q.n}.question`, q.question);
            for (const o of q.options) t(`quiz.${project}.${q.n}.option.${o.letter}`, o.text);
        }
    }
    // The closed-book set asked after the task, which is per project too.
    for (const [project, questions] of Object.entries(AFTER_QUIZZES)) {
        for (const q of questions) {
            t(`after.${project}.${q.n}.question`, q.question);
            for (const o of q.options) t(`after.${project}.${q.n}.option.${o.letter}`, o.text);
        }
    }
    assert.deepEqual(missingKeys(), [], 'these would render in English among Chinese options');
});

test('a missing key shows English rather than the key', () => {
    setLanguage('zh-Hans');
    assert.equal(t('nothing.here.at.all', 'the English words'), 'the English words');
    assert.ok(missingKeys().includes('nothing.here.at.all'), 'and it is recorded');
});
