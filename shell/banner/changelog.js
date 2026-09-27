/* ECCSA-Shell · changelog.js — estado del popup de novedades.
   Generado por tools/sync_shell.py — NO editar a mano.

   Por qué existe: el modal de novedades (banner/Changelog.svelte) tiene que
   saltar solo la PRIMERA vez que se ve una versión nueva, y el botón 📋 de la
   barra de acciones tiene que poder abrirlo desde cualquier página. Como el
   botón vive en el header de cada página y el modal tiene que vivir en el
   layout (si no, no saltaría al entrar por cualquier ruta), hace falta un
   estado compartido. Este módulo es ese estado — y también el que recuerda
   qué versión ya se mostró.

   Importalo desde el shell, no lo reescribas:
     import { abrirChangelog, cerrarChangelog, versionYaVista,
              marcarVersionVista } from '$lib/changelog.js';
*/
import { writable } from 'svelte/store';

export const changelogAbierto = writable(false);

export function abrirChangelog() {
	changelogAbierto.set(true);
}

export function cerrarChangelog() {
	changelogAbierto.set(false);
}

/* Una sola clave por app con la ÚLTIMA versión vista (no una por versión):
   así no crece sin límite localStorage aunque la app se actualice muchas
   veces. El prefijo evita chocar con otras claves del mismo origen. */
export const claveChangelog = (appId) => `eccsa:changelog:${appId}`;

export function versionYaVista(appId) {
	try {
		return localStorage.getItem(claveChangelog(appId));
	} catch (e) {
		/* modo privado / storage bloqueado: se comporta como si no se hubiera
		   mostrado nunca, que es el caso seguro (volver a mostrar el popup). */
		return null;
	}
}

export function marcarVersionVista(appId, version) {
	try {
		localStorage.setItem(claveChangelog(appId), String(version));
	} catch (e) {
		/* sin storage no hay memoria entre recargas: el popup volverá a
		   saltar solo. No es grave, es la degradación esperable. */
	}
}
