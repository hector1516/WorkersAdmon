// Texto de interfaz compartido.

// Cifra con su sustantivo en singular o plural: `plural(1, 'clave', 'claves')`
// devuelve «1 clave» y no «1 clave(s)». El cero va en plural.
export function plural(n, singular, plural_) {
	const c = Number(n) || 0;
	return `${c} ${c === 1 ? singular : plural_}`;
}
