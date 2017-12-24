# hook_tools

Estas son herramientas que ayudan a mantener el código en los distintos
repositorios del proyecto [omegaUp](https://omegaup.com) con un estilo
consistente.

Para agregar `hook_tools` a tu repositorio:

* Agrega [hook_tools](https://github.com/omegaup/hook_tools/) como submódulo de
  git en algún lugar de tu repositorio.
* Agrega un archivo `.lint.config.json` en la raíz de tu repositorio.
* Invoca `hook_tools/lint.py validate --all` en tu archivo `.travis.yml` o en
  los git pre-upload hooks.

## `.lint.config.json`

Este es un diccionario de configuración de los linters que se van a correr. Los
linters soportados (con sus respectivas opciones) son:

* `whitespace`: Elimina molestos espacios en blanco, como espacios al final de
  la línea, múltiples líneas vacías, saltos de línea estilo Windows.
* `javascript`: Corre el linter del Google Closure Compiler, seguido de
  ClangFormat.
  * `extra_js_linters`: Un arreglo con comandos que se van a correr.
* `html`: Corre HTML Tidy.
  * `strict`: Un bool que indica si se va a correr en modo estricto
* `vue`: Corre los linters de `javascript` y `html` en las distintas secciones
  de un Vue template.
  * `extra_js_linters`: Un arreglo con comandos que se van a correr en la
    sección `<script>..</script>` del template.
* `php`: Corre PHP Code Beautifier.
  * `standard`: Una cadena con la ruta del estándar de phpcbf.
* `python`: Corre pep8 y pylint.
  * `pep8_config`: Una cadena con la ruta del archivo de configuración para
     pep8.
  * `pylint_config`: Una cadena con la ruta del archivo de configuración para
     pylint.
* `custom`: Corre comandos personalizados.
  * `commands`: Un arreglo con comandos.

Todos los linters soportan dos opciones adicionales:

* `whitelist`: Un arreglo con expresiones regulares. Los archivos a considerar
  para el linter actual deben de hacer match con _al menos un_ regex de este
  arreglo.
* `blacklist`: Un arreglo con expresiones regulares. Los archivos a considerar
  para el linter actual deben de _no_ hacer match con _ningún_ regex de este
  arreglo.

## Comandos personalizados

Tanto el linter `custom` como `javascript` soportan comandos personalizados.
Estos comandos se van a correr tal-cual con dos argumentos extra: el nombre del
archivo (temporal) que debe de actualizarse con el contenido correctamente
formateado, y el nombre de archivo original (si se desea escribir información
de depuración a stderr). Este comando se ejecutará mediante `/bin/bash`.
