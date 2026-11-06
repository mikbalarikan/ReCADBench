# backends/nx/adapter

`recad nx export | extract | set-param | replay | version`. `adapter.py` is the host side (stdlib),
`nx_adapter_journal.py` + `nx_measure.py` run inside NX, `nx_taxonomy.py` is the pure mapping /
counting rule (CI-tested). Mapping table and native-model contract: [`../README.md`](../README.md).
