import { describe, it, expect } from 'vitest';
import { isNotebook, notebookCellForRef, type NotebookCellText } from '../state/notebook-cells';

// A `codoc:` citation of a notebook step has to land on the cell that holds it. These pin
// the two halves of that: a notebook is recognized before any code path can try (a
// `.ipynb` read as text is JSON, and the regex fallback reveals a line of base64 PNG), and
// a cell is found by the heading grammar the chunker used to NAME it.
//
// Parity with `codoc/lang/notebook.py` is the contract: it decides what a chunk is called,
// this decides which cell that name is in, and a disagreement is a citation pointing at
// the wrong step. `tests/test_notebook_lang.py` holds the other half.

const code = (text: string): NotebookCellText => ({ kind: 'code', text });
const md = (text: string): NotebookCellText => ({ kind: 'markup', text });

const CHURN: NotebookCellText[] = [
    md('# Churn model\nFrom the raw export to a fitted model.'),
    code('import pandas as pd\nfrom pathlib import Path'),
    md('## Load the data\nOne file per month.'),
    code("RAW = Path('data')\ndf = pd.concat(RAW.glob('*.csv'))"),
    md('## Feature engineering'),
    code("def tenure(row):\n    return row.days\n\ndf['tenure'] = df.apply(tenure)"),
    md('## Train\nOne fold, because the export is small.'),
    code('class Model:\n    def fit(self, x, y):\n        return self\n\nm = Model().fit(X, y)'),
    md('## Train\nAgain, with the engineered column.'),
    code('m2 = Model().fit(X2, y)'),
];

describe('isNotebook', () => {
    it('claims the extension before any path that would reveal JSON', () => {
        expect(isNotebook('work/churn.ipynb')).toBe(true);
        expect(isNotebook('WORK/CHURN.IPYNB')).toBe(true);
        expect(isNotebook('codoc/loop/apply.py')).toBe(false);
        expect(isNotebook('notes/churn.ipynb.bak')).toBe(false);
    });
});

describe('notebookCellForRef', () => {
    it('lands a section on the heading that names it, qualified or bare', () => {
        // The heading and not the first statement: it is the line that says which section
        // this is, and both are on screen once the cell is revealed.
        expect(notebookCellForRef(CHURN, 'work/churn.ipynb::load-the-data')).toEqual({ cell: 2, line: 0 });
        expect(notebookCellForRef(CHURN, 'load-the-data')).toEqual({ cell: 2, line: 0 });
    });

    it('lands a member on the line that declares it', () => {
        expect(notebookCellForRef(CHURN, 'feature-engineering.tenure')).toEqual({ cell: 5, line: 0 });
        expect(notebookCellForRef(CHURN, 'train.Model')).toEqual({ cell: 7, line: 0 });
        expect(notebookCellForRef(CHURN, 'train.Model.fit')).toEqual({ cell: 7, line: 1 });
        expect(notebookCellForRef(CHURN, 'load-the-data.df')).toEqual({ cell: 3, line: 1 });
    });

    it('reads a repeated heading as its own section, so a member is not filed under the first', () => {
        expect(notebookCellForRef(CHURN, 'train[1]')).toEqual({ cell: 8, line: 0 });
        expect(notebookCellForRef(CHURN, 'train[1].m2')).toEqual({ cell: 9, line: 0 });
        expect(notebookCellForRef(CHURN, 'train.m')).toEqual({ cell: 7, line: 4 });
    });

    it('resolves a heading-less notebook the way a script resolves', () => {
        const script = [code('import os\n\ndef go():\n    return os.getcwd()')];
        expect(notebookCellForRef(script, 'p.ipynb::go')).toEqual({ cell: 0, line: 2 });
        expect(notebookCellForRef(script, '__module__')).toEqual({ cell: 0, line: 0 });
    });

    it('sends __module__ to the first cell holding code, not to the title', () => {
        // Cell zero is a title here, so the imports belong to `# Churn model` — and the
        // chunker emits no `__module__` for this notebook at all. The unnamed run only
        // exists when code comes before the first heading, and then it is where the glue is.
        expect(notebookCellForRef(CHURN, '__module__')).toBeNull();
        const preamble = [code('import sys'), md('## Load'), code('x = 1')];
        expect(notebookCellForRef(preamble, '__module__')).toEqual({ cell: 0, line: 0 });
        expect(notebookCellForRef(preamble, 'load.x')).toEqual({ cell: 2, line: 0 });
    });

    it('lands a level-one title on itself — its cells are its glue, not its members', () => {
        // `# Churn model` names the section the imports belong to, and an import is glue:
        // the chunker emits `churn-model` and no member for `pd`, so there is no address
        // here for this to resolve and no cell for it to invent.
        expect(notebookCellForRef(CHURN, 'churn-model')).toEqual({ cell: 0, line: 0 });
    });

    it('starts a section at a heading part-way down a cell', () => {
        const cells = [md('Intro line.\n\n## Second step\nWhat it does.'), code('x = 1')];
        expect(notebookCellForRef(cells, 'second-step')).toEqual({ cell: 0, line: 2 });
        expect(notebookCellForRef(cells, 'second-step.x')).toEqual({ cell: 1, line: 0 });
    });

    it('addresses a heading written in another script', () => {
        const cells = [md('## 加载数据'), code('x = 1')];
        expect(notebookCellForRef(cells, '加载数据')).toEqual({ cell: 0, line: 0 });
    });

    it('keeps a dotted heading out of the member namespace, as the chunker does', () => {
        const cells = [md('## Step 1.2'), code('y = 2')];
        expect(notebookCellForRef(cells, 'step-1-2')).toEqual({ cell: 0, line: 0 });
    });

    it('finds a name only where it is declared, never where it is used', () => {
        const cells = [
            md('## Fit'),
            code('score = evaluate(model)'),
            code('model = build()'),
        ];
        expect(notebookCellForRef(cells, 'fit.model')).toEqual({ cell: 2, line: 0 });
    });

    it('falls back to the section heading when a member is gone', () => {
        // The citation named a step that exists; the heading is the honest answer to
        // where that step is, and the reader can see for themselves that the name is not
        // in it.
        expect(notebookCellForRef(CHURN, 'train.vanished')).toEqual({ cell: 6, line: 0 });
    });

    it('is null for a section that does not exist, rather than a cell it picked', () => {
        expect(notebookCellForRef(CHURN, 'evaluate')).toBeNull();
        expect(notebookCellForRef([], 'anything')).toBeNull();
        expect(notebookCellForRef(CHURN, '')).toBeNull();
    });
});
