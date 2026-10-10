// The reader's theme choice: "dark", "light", or absent (follow the system). Stored per browser only.

export const THEME_KEY = "cc-theme";

/** Runs in <head> before first paint so an explicit choice never flashes the other theme. */
export const THEME_INIT_SCRIPT = `try{var t=localStorage.getItem("${THEME_KEY}");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}`;
