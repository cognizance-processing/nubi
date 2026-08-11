/**
 * PricingCalculator.jsx — EE wrapper re-using the core PricingCalculator.
 *
 * The core component lives at src/components/pricing/PricingCalculator.jsx.
 * The EE version is a thin pass-through: it receives the same fxRate prop and
 * forwards it, along with the static fallback competitor lists from pricing.js.
 *
 * EE-specific additions (none at present — checkout CTAs belong on PricingPage,
 * not the calculator) can be layered here in future.
 */

export { default } from '../../components/pricing/PricingCalculator.jsx'
