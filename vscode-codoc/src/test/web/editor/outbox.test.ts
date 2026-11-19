import { describe, expect, it } from 'vitest';

import { Outbox } from '../../../web/editor/outbox';

describe('the ops applied for this page', () => {
    it('are forgotten once a projection frontier covers them', () => {
        const o = new Outbox(new Map(), () => undefined);
        o.ack([{ cid: 'c1', op: 's:3', conflict: null }, { cid: 'c2', op: 's:4', conflict: null }]);
        o.covered({ s: 3 });
        expect([...o.applied]).toEqual(['s:4']);
    });
});
