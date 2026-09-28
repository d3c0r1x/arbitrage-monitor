/**
 * PancakeSwap read-only sidecar (no private keys).
 *
 * Commands via argv:
 *   node index.js price  --chainId 56 --addresses 0x..,0x..
 *   node index.js quote  --rpc <url> --tokenIn 0x.. --tokenOut 0x.. --amountIn <wei>
 *                        [--decimalsIn 18] [--decimalsOut 18] [--maxHops 2]
 *
 * Or JSON on stdin: { "cmd": "quote"|"price", ... }
 *
 * Price uses the same HTTP API as @pancakeswap/price-api-sdk
 * (wallet-api.pancakeswap.com) — the npm package currently cannot install
 * cleanly due to a missing @pancakeswap/utils dependency on the registry.
 */

const { createPublicClient, http, parseAbiItem, getAddress } = require("viem");
const { bsc, mainnet, base, arbitrum, polygon } = require("viem/chains");
const { SmartRouter, InfinityRouter } = require("@pancakeswap/smart-router");
const { CurrencyAmount, Token, TradeType, ChainId } = require("@pancakeswap/sdk");

let GraphQLClient = null;
try {
  GraphQLClient = require("graphql-request").GraphQLClient;
} catch (_) {
  GraphQLClient = null;
}

const CHAINS = {
  1: mainnet,
  56: bsc,
  8453: base,
  42161: arbitrum,
  137: polygon,
};

const PRICE_API = process.env.PCS_PRICE_API || "https://wallet-api.pancakeswap.com";
const SUBGRAPH_V3 = process.env.PCS_SUBGRAPH_V3_BSC || process.env.PCS_SUBGRAPH_V3 || "";

function parseArgs(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith("--")) {
      const key = a.slice(2);
      const val = argv[i + 1] && !argv[i + 1].startsWith("--") ? argv[++i] : "true";
      out[key] = val;
    } else {
      out._.push(a);
    }
  }
  return out;
}

async function readStdinJson() {
  if (process.stdin.isTTY) return null;
  const chunks = [];
  for await (const c of process.stdin) chunks.push(c);
  const raw = Buffer.concat(chunks).toString("utf8").trim();
  if (!raw) return null;
  return JSON.parse(raw);
}

async function cmdPrice({ chainId, addresses }) {
  const cid = Number(chainId);
  const addrs = String(addresses)
    .split(",")
    .map((a) => a.trim().toLowerCase())
    .filter(Boolean);
  if (!cid || !addrs.length) {
    throw new Error("price requires chainId and addresses");
  }
  const key = encodeURIComponent(addrs.map((a) => `${cid}:${a}`).join(","));
  // preview=1 avoids 429 on non-frontend clients (same as price-api-sdk non-prod).
  const url = `${PRICE_API}/v1/prices/list/${key}?preview=1`;
  const t0 = performance.now();
  const res = await fetch(url, {
    headers: { accept: "application/json", "user-agent": "mexc-dex-arb-pcs-sidecar/1.0" },
  });

  const ms = performance.now() - t0;
  if (!res.ok) {
    throw new Error(`price_api_http_${res.status}`);
  }
  const data = await res.json();
  const prices = {};
  for (const a of addrs) {
    const k = `${cid}:${a}`;
    prices[a] = Number(data[k] ?? data[`${cid}:${a}`] ?? 0);
  }
  return { ok: true, chainId: cid, prices, latency_ms: Math.round(ms), source: "wallet-api.pancakeswap.com" };
}

async function erc20Meta(client, address) {
  const abi = [
    parseAbiItem("function decimals() view returns (uint8)"),
    parseAbiItem("function symbol() view returns (string)"),
  ];
  const [decimals, symbol] = await Promise.all([
    client.readContract({ address, abi, functionName: "decimals" }).catch(() => 18),
    client.readContract({ address, abi, functionName: "symbol" }).catch(() => "TKN"),
  ]);
  return { decimals: Number(decimals), symbol: String(symbol || "TKN").slice(0, 12) };
}

async function cmdQuote(opts) {
  const chainId = Number(opts.chainId || 56);
  const chain = CHAINS[chainId];
  if (!chain) throw new Error(`unsupported_chainId_${chainId}`);
  const rpc = opts.rpc || process.env.PCS_RPC_URL;
  if (!rpc) throw new Error("rpc required ( --rpc or PCS_RPC_URL )");

  const tokenInAddr = getAddress(opts.tokenIn);
  const tokenOutAddr = getAddress(opts.tokenOut);
  const amountIn = BigInt(opts.amountIn);
  if (!tokenInAddr || !tokenOutAddr || amountIn <= 0n) {
    throw new Error("quote requires tokenIn, tokenOut, amountIn");
  }

  const maxHops = Number(opts.maxHops || 2);
  const maxSplits = Number(opts.maxSplits || 1);
  const preferInfinity = String(opts.preferInfinity || "1") !== "0";

  const client = createPublicClient({
    chain,
    transport: http(rpc, { timeout: 45_000 }),
    batch: { multicall: true },
  });

  const tMeta = performance.now();
  const [metaIn, metaOut] = await Promise.all([
    opts.decimalsIn
      ? { decimals: Number(opts.decimalsIn), symbol: opts.symbolIn || "IN" }
      : erc20Meta(client, tokenInAddr),
    opts.decimalsOut
      ? { decimals: Number(opts.decimalsOut), symbol: opts.symbolOut || "OUT" }
      : erc20Meta(client, tokenOutAddr),
  ]);
  const meta_ms = performance.now() - tMeta;

  const tokenIn = new Token(chainId, tokenInAddr, metaIn.decimals, metaIn.symbol);
  const tokenOut = new Token(chainId, tokenOutAddr, metaOut.decimals, metaOut.symbol);
  const amount = CurrencyAmount.fromRawAmount(tokenIn, amountIn);

  let subgraphProvider = undefined;
  if (GraphQLClient && SUBGRAPH_V3 && chainId === 56) {
    const gql = new GraphQLClient(SUBGRAPH_V3, {
      headers: { "content-type": "application/json" },
    });
    subgraphProvider = () => gql;
  }

  const tPools = performance.now();
  const [v2Pools, v3Pools, stablePools] = await Promise.all([
    SmartRouter.getV2CandidatePools({
      onChainProvider: () => client,
      currencyA: tokenIn,
      currencyB: tokenOut,
    }).catch((e) => {
      console.error("v2_pools", e.message);
      return [];
    }),
    SmartRouter.getV3CandidatePools({
      onChainProvider: () => client,
      subgraphProvider,
      currencyA: tokenIn,
      currencyB: tokenOut,
    }).catch((e) => {
      console.error("v3_pools", e.message);
      return [];
    }),
    SmartRouter.getStableCandidatePools({
      onChainProvider: () => client,
      currencyA: tokenIn,
      currencyB: tokenOut,
    }).catch(() => []),
  ]);
  const pools = [...v2Pools, ...v3Pools, ...stablePools];
  const pools_ms = performance.now() - tPools;

  const tTrade = performance.now();
  let trade = null;
  let engine = null;
  let tradeError = null;

  if (preferInfinity && pools.length) {
    try {
      trade = await InfinityRouter.getBestTrade(amount, tokenOut, TradeType.EXACT_INPUT, {
        gasPriceWei: () => client.getGasPrice(),
        candidatePools: pools,
        maxHops,
        maxSplits,
      });
      if (trade) engine = "InfinityRouter";
    } catch (e) {
      tradeError = e.message;
    }
  }

  if (!trade && pools.length) {
    try {
      const quoteProvider = SmartRouter.createQuoteProvider({
        onChainProvider: () => client,
      });
      trade = await SmartRouter.getBestTrade(amount, tokenOut, TradeType.EXACT_INPUT, {
        gasPriceWei: () => client.getGasPrice(),
        maxHops,
        maxSplits,
        poolProvider: SmartRouter.createStaticPoolProvider(pools),
        quoteProvider,
        quoterOptimization: true,
      });
      if (trade) engine = "SmartRouter";
    } catch (e) {
      tradeError = e.message;
    }
  }
  const trade_ms = performance.now() - tTrade;

  if (!trade) {
    return {
      ok: false,
      error: tradeError || "no_route",
      pools: pools.length,
      pools_v2: v2Pools.length,
      pools_v3: v3Pools.length,
      pools_stable: stablePools.length,
      latency_ms: {
        meta: Math.round(meta_ms),
        pools: Math.round(pools_ms),
        trade: Math.round(trade_ms),
        total: Math.round(meta_ms + pools_ms + trade_ms),
      },
    };
  }

  const outRaw = trade.outputAmount.quotient.toString();
  return {
    ok: true,
    engine,
    chainId,
    tokenIn: tokenInAddr.toLowerCase(),
    tokenOut: tokenOutAddr.toLowerCase(),
    amountIn: amountIn.toString(),
    amountOut: outRaw,
    amountOutHuman: trade.outputAmount.toSignificant(8),
    pools_candidates: pools.length,
    pools_v2: v2Pools.length,
    pools_v3: v3Pools.length,
    pools_stable: stablePools.length,
    hops: null,
    gas_estimate: trade.gasEstimate?.toString?.() || null,
    latency_ms: {
      meta: Math.round(meta_ms),
      pools: Math.round(pools_ms),
      trade: Math.round(trade_ms),
      total: Math.round(meta_ms + pools_ms + trade_ms),
    },
  };
}

async function main() {
  const stdin = await readStdinJson();
  const args = parseArgs(process.argv.slice(2));
  const cmd = (stdin && stdin.cmd) || args._[0] || args.cmd;
  const opts = { ...args, ...(stdin || {}) };

  try {
    let result;
    if (cmd === "price") {
      result = await cmdPrice(opts);
    } else if (cmd === "quote") {
      result = await cmdQuote(opts);
    } else if (cmd === "ping") {
      result = { ok: true, smartRouter: true, priceApi: PRICE_API, chainId: ChainId.BSC };
    } else {
      result = { ok: false, error: `unknown_cmd_${cmd}` };
    }
    process.stdout.write(JSON.stringify(result) + "\n");
    process.exit(result.ok === false && result.error && result.error !== "no_route" ? 2 : 0);
  } catch (err) {
    process.stdout.write(
      JSON.stringify({ ok: false, error: String(err && err.message ? err.message : err) }) + "\n"
    );
    process.exit(1);
  }
}

main();
