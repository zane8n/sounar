# Sounar's upmix plugin (LADSPA). `make` builds it here; install.sh copies it to
# ~/.local/lib/ladspa. tests/test_upmix_plugin.py tests it (it builds it when a compiler is there).
CFLAGS ?= -O2 -Wall -Wextra -fPIC -fvisibility=hidden -ffp-contract=off

sounar_upmix.so: sounar_upmix.c
	$(CC) $(CFLAGS) -shared -o $@ $< -lm

clean:
	rm -f sounar_upmix.so

.PHONY: clean
