# QuantToken build

`QuantToken.json`: ABI + bytecode of `../QuantToken.sol` compiled against **OpenZeppelin Contracts 5.4.0**
with solc 0.8.30 (optimizer, 200 runs, EVM `cancun`) using forge 1.5.1:

```bash
forge build --contracts src --remappings '@openzeppelin/contracts@5.4.0/=lib/oz/' \
  --use 0.8.30 --optimize --optimizer-runs 200 --evm-version cancun
```

It is the same file the class 5 compose stack deploys (`modulo-2/clase_2/docker/deployer/QuantToken.json`).
In class, Remix compiles the contract itself and writes its own `artifacts/` folder in the browser
workspace: this one is only a reference build.
