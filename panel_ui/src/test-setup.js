// jsdom no implementa scrollTo y llena la salida de "Not implemented".
// El router lo llama al navegar; aquí solo se silencia.
window.scrollTo = () => {};
