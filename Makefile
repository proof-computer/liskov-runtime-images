.PHONY: validate test fingerprint build-v4-control build-debian-trixie repro-v4-control repro-debian-trixie

validate:
	scripts/validate-change.sh

test:
	python3 -m unittest discover -s tests -v
	python3 -m compileall -q scripts tests

fingerprint:
	python3 scripts/change-classifier.py fingerprint

build-v4-control:
	python3 scripts/build-image.py v4-control --output-dir out/v4-control

build-debian-trixie:
	python3 scripts/build-image.py debian-trixie --output-dir out/debian-trixie

repro-v4-control:
	scripts/verify-reproducible.sh v4-control

repro-debian-trixie:
	scripts/verify-reproducible.sh debian-trixie
