// The question round's clock, as rules rather than as a rendering.
//
// Kept out of app.js so the two decisions it encodes can be tested without a DOM:
// how long the sitting runs, and when the completeness gate must stand aside.

/**
 * How long the open-book question round runs for.
 *
 * Long enough to look twelve answers up, short enough that somebody cannot read
 * the whole codebase and answer from that instead — which would erase the
 * difference between the two ways of working, since both can reach every answer
 * given unlimited time. The limit is what makes the SCORE mean something.
 */
export const QUIZ_MINUTES = 10;

/** How long before the end the clock starts warning (ms). Enough to finish the
 *  question in hand; not so long that it nags for a quarter of the sitting. */
export const QUIZ_WARN_MS = 30_000;

/** A beat between the clock reaching zero and the page changing, so the last
 *  thing they see is the timer running out rather than a new screen arriving
 *  with no explanation. */
export const QUIZ_ADVANCE_DELAY_MS = 1_200;

/**
 * May the session move past the question round?
 *
 * Yes when every question is answered — a blank is indistinguishable from "I do
 * not know", and which wrong option drew somebody is most of what a wrong answer
 * tells us. Also yes when the clock ran out, whatever is on screen: that is the
 * one case where a blank means something by itself, and holding somebody on a
 * step they are out of time for would make the button, not the timer, the thing
 * in charge of a timed test.
 */
export function timedOutAllowsAdvance({ answered, timedOut }) {
    return Boolean(answered || timedOut);
}
