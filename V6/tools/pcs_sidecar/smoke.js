const { createPublicClient, http } = require("viem");
const { bsc } = require("viem/chains");
const { SmartRouter, InfinityRouter } = require("@pancakeswap/smart-router");
const { CurrencyAmount, Token, TradeType } = require("@pancakeswap/sdk");

async function main() {
  const rpc = process.env.PCS_RPC_URL || process.argv[2];
  if (!rpc) throw new Error("pass rpc url");
  const client = createPublicClient({ chain: bsc, transport: http(rpc, { timeout: 30000 }), batch: { multicall: true } });
  const cake = new Token(56, "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82", 18, "CAKE");
  const usdt = new Token(56, "0x55d398326f99059fF775485246999027B3197955", 18, "USDT");
  console.log("gas", (await client.getGasPrice()).toString());

  const t0 = Date.now();
  let v2 = [], v3 = [], st = [];
  try {
    v2 = await SmartRouter.getV2CandidatePools({ onChainProvider: () => client, currencyA: cake, currencyB: usdt });
  } catch (e) { console.log("v2 err", e.message); }
  try {
    v3 = await SmartRouter.getV3CandidatePools({ onChainProvider: () => client, subgraphProvider: undefined, currencyA: cake, currencyB: usdt });
  } catch (e) { console.log("v3 err", e.message); }
  try {
    st = await SmartRouter.getStableCandidatePools({ onChainProvider: () => client, currencyA: cake, currencyB: usdt });
  } catch (e) { console.log("st err", e.message); }
  console.log("pools", { v2: v2.length, v3: v3.length, st: st.length, ms: Date.now() - t0 });

  const amount = CurrencyAmount.fromRawAmount(cake, 10n ** 18n);
  const quoteProvider = SmartRouter.createQuoteProvider({ onChainProvider: () => client });
  const pools = [...v2, ...v3, ...st];
  try {
    const trade = await SmartRouter.getBestTrade(amount, usdt, TradeType.EXACT_INPUT, {
      gasPriceWei: () => client.getGasPrice(),
      maxHops: 2,
      maxSplits: 1,
      poolProvider: SmartRouter.createStaticPoolProvider(pools),
      quoteProvider,
    });
    console.log("smart", trade ? trade.outputAmount.toSignificant(6) : null);
  } catch (e) {
    console.log("smart err", e.message);
  }

  try {
    const trade2 = await InfinityRouter.getBestTrade(amount, usdt, TradeType.EXACT_INPUT, {
      gasPriceWei: () => client.getGasPrice(),
      candidatePools: pools,
    });
    console.log("inf", trade2 ? trade2.outputAmount.toSignificant(6) : null);
  } catch (e) {
    console.log("inf err", e.message);
  }
}

main().catch((e) => { console.error(e); process.exit(1); });
