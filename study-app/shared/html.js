// Escaping, in one place.
//
// It lived in the participant page and was used, undefined, in the dashboard,
// where it threw the first time a researcher clicked New. Two copies would have
// been a smell; one copy and one user was a bug.
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
