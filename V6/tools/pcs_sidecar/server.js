/**
 * Persistent stdio JSON-RPC sidecar.
 * One JSON object per line on stdin → one JSON line on stdout.
 * Keeps viem client / module cache warm across quotes.
 */
const { createPublicClient, http, parseAbiItem, getAddress } = require("viem");
const { bsc, mainnet, base, arbitrum, polygon } = require("viem/chains");
const { SmartRouter, InfinityRouter } = require("@pancakeswap/smart-router");
const { CurrencyAmount, Token, TradeType, ChainId } = require("@pancakeswap/sdk");
const { GraphQLClient } = require("graphql-request");
const readline = require("readline");

const CHAINS = { 1: mainnet, 56: bsc, 8453: base, 42161: arbitrum, 137: polygon };
const PRICE_API = process.env.PCS_PRICE_API || "https://wallet-api.pancakeswap.com";
const SUBGRAPH_V3 = process.env.PCS_SUBGRAPH_V3_BSC || process.env.PCS_SUBGRAPH_V3 || "";

const clients = new Map();

function getClient(chainId, rpc) {
  const key = `${chainId}|${rpc}`;
  if (!clients.has(key)) {
    const chain = CHAINS[chainId];
    clients.set(
      key,
      createPublicClient({
        chain,
        transport: http(rpc, { timeout: 45_000 }),
        batch: { multicall: true },
      })
    );
  }
  return clients.get(key);
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

async function cmdPrice({ chainId, addresses }) {
  const cid = Number(chainId);
  const addrs = String(addresses)
    .split(",")
    .map((a) => a.trim().toLowerCase())
    .filter(Boolean);
  const key = encodeURIComponent(addrs.map((a) => `${cid}:${a}`).join(","));
  const url = `${PRICE_API}/v1/prices/list/${key}?preview=1`;
  const t0 = performance.now();
  const res = await fetch(url, {
    headers: { accept: "application/json", "user-agent": "mexc-dex-arb-pcs-sidecar/1.0" },
  });
  const ms = performance.now() - t0;
  if (!res.ok) throw new Error(`price_api_http_${res.status}`);
  const data = await res.json();
  const prices = {};
  for (const a of addrs) prices[a] = Number(data[`${cid}:${a}`] ?? 0);
  return { ok: true, chainId: cid, prices, latency_ms: Math.round(ms), source: "wallet-api.pancakeswap.com" };
}

async function cmdQuote(opts) {
  const chainId = Number(opts.chainId || 56);
  const rpc = opts.rpc || process.env.PCS_RPC_URL;
  if (!rpc) throw new Error("rpc required");
  const tokenInAddr = getAddress(opts.tokenIn);
  const tokenOutAddr = getAddress(opts.tokenOut);
  const amountIn = BigInt(opts.amountIn);
  const maxHops = Number(opts.maxHops || 2);
  const maxSplits = Number(opts.maxSplits || 1);
  const skipV3 = String(opts.skipV3 || "0") === "1";
  const client = getClient(chainId, rpc);

  const tMeta = performance.now();
  const [metaIn, metaOut] = await Promise.all([
    opts.decimalsIn != null
      ? { decimals: Number(opts.decimalsIn), symbol: opts.symbolIn || "IN" }
      : erc20Meta(client, tokenInAddr),
    opts.decimalsOut != null
      ? { decimals: Number(opts.decimalsOut), symbol: opts.symbolOut || "OUT" }
      : erc20Meta(client, tokenOutAddr),
  ]);
  const meta_ms = performance.now() - tMeta;

  const tokenIn = new Token(chainId, tokenInAddr, metaIn.decimals, metaIn.symbol);
  const tokenOut = new Token(chainId, tokenOutAddr, metaOut.decimals, metaOut.symbol);
  const amount = CurrencyAmount.fromRawAmount(tokenIn, amountIn);

  let subgraphProvider;
  if (!skipV3 && SUBGRAPH_V3 && chainId === 56) {
    const gql = new GraphQLClient(SUBGRAPH_V3);
    subgraphProvider = () => gql;
  }

  const tPools = performance.now();
  const tasks = [
    SmartRouter.getV2CandidatePools({
      onChainProvider: () => client,
      currencyA: tokenIn,
      currencyB: tokenOut,
    }).catch(() => []),
    skipV3
      ? Promise.resolve([])
      : SmartRouter.getV3CandidatePools({
          onChainProvider: () => client,
          subgraphProvider,
          currencyA: tokenIn,
          currencyB: tokenOut,
        }).catch(() => []),
    SmartRouter.getStableCandidatePools({
      onChainProvider: () => client,
      currencyA: tokenIn,
      currencyB: tokenOut,
    }).catch(() => []),
  ];
  const [v2Pools, v3Pools, stablePools] = await Promise.all(tasks);
  const pools = [...v2Pools, ...v3Pools, ...stablePools];
  const pools_ms = performance.now() - tPools;

  const tTrade = performance.now();
  let trade = null;
  let engine = null;
  let tradeError = null;
  if (pools.length) {
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
      const quoteProvider = SmartRouter.createQuoteProvider({ onChainProvider: () => client });
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

  return {
    ok: true,
    engine,
    chainId,
    tokenIn: tokenInAddr.toLowerCase(),
    tokenOut: tokenOutAddr.toLowerCase(),
    amountIn: amountIn.toString(),
    amountOut: trade.outputAmount.quotient.toString(),
    amountOutHuman: trade.outputAmount.toSignificant(8),
    pools_candidates: pools.length,
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

async function handle(msg) {
  const cmd = msg.cmd;
  if (cmd === "ping") {
    return {
      ok: true,
      smartRouter: true,
      infinityRouter: true,
      priceApi: PRICE_API,
      subgraph: Boolean(SUBGRAPH_V3),
      chainId: ChainId.BSC,
    };
  }
  if (cmd === "price") return cmdPrice(msg);
  if (cmd === "quote") return cmdQuote(msg);
  return { ok: false, error: `unknown_cmd_${cmd}` };
}

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on("line", async (line) => {
  const raw = line.trim();
  if (!raw) return;
  let msg;
  try {
    msg = JSON.parse(raw);
  } catch (e) {
    process.stdout.write(JSON.stringify({ ok: false, error: "bad_json" }) + "\n");
    return;
  }
  try {
    const result = await handle(msg);
    process.stdout.write(JSON.stringify(result) + "\n");
  } catch (err) {
    process.stdout.write(
      JSON.stringify({ ok: false, error: String(err && err.message ? err.message : err) }) + "\n"
    );
  }
});

process.stdout.write(JSON.stringify({ ok: true, ready: true, cmd: "hello" }) + "\n");
