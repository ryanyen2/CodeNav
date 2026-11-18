import { describe, expect, it } from 'vitest';
import { EditorState, TextSelection } from '@tiptap/pm/state';

import { citeItems, triggerAt } from '../../../web/editor/menus';
import { codocSchema } from '../../../web/editor/schema';

const schema = codocSchema();
const para = (bid: string, text: string) => schema.nodes.paragraph.create({ bid, head: 1 }, text ? schema.text(text) : undefined);

describe('menus', () => {
    it('opens / only at the start of a block and @ after whitespace', () => {
        const doc = schema.nodes.doc.create(null, [para('a', '/he'), para('b', 'see @cli'), para('c', 'x/y')]);
        const at = (pos: number) => triggerAt(EditorState.create({ schema, doc, selection: TextSelection.create(doc, pos) }));
        expect(at(4)).toEqual({ kind: '/', from: 1, query: 'he' });          // "/he|"
        expect(at(14)).toEqual({ kind: '@', from: 10, query: 'cli' });       // "see @cli|"
        expect(at(19)).toBeNull();                                           // "x/y|"
    });
    it('ranks cite candidates by leaf prefix, then substring, and dedups', () => {
        const bindings = { by_feature: { h1: [{ file: 'c.py', symbol: 'c.py::Client' }, { file: 'c.py', symbol: 'c.py::Client.send' }],
                                         h2: [{ file: 'c.py', symbol: 'c.py::Client' }, { file: 's.py', symbol: 's.py::save' }] } };
        expect(citeItems(bindings, 'cl').map(i => i.label)).toEqual(['Client', 'Client.send']);
        expect(citeItems(bindings, '').length).toBe(3);
        expect(citeItems(bindings, 'zzz')).toEqual([]);
    });
});
