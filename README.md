# dawnpy-lwm2m

LwM2M transport extension for Dawn Wakaama targets, built on `aiocoap`.

Main Dawn project: [railab/dawn](https://github.com/railab/dawn).

The package exposes the standalone `dawnpy-lwm2m` command. It starts a local
LwM2M server, waits for a Wakaama client registration, and provides a
descriptor-backed console for reading and writing resources by IO ID or by
absolute LwM2M path.

```sh
dawnpy-lwm2m descriptors/ntfc/ntfc_wakaama.yaml --endpoint ntfc-wakaama
```

QA follows the shared Python tool baseline:

```sh
tox
tox -e py
tox -e format
tox -e flake8
tox -e type
```
