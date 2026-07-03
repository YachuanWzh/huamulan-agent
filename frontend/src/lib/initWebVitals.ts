import type { Metric } from 'web-vitals'
import { onCLS, onFCP, onINP, onLCP, onTTFB } from 'web-vitals'

/**
 * Registers callbacks for all five Core Web Vitals and reports them
 * through the provided callback whenever a metric value is ready.
 *
 * Metrics collected: LCP (Largest Contentful Paint), INP (Interaction to
 * Next Paint), CLS (Cumulative Layout Shift), TTFB (Time to First Byte),
 * and FCP (First Contentful Paint).
 *
 * @param reportWebVital - callback receiving (metricName, metricValue)
 */
export function initWebVitals(
  reportWebVital: (name: string, value: number) => void,
): void {
  onLCP((metric: Metric) => reportWebVital('LCP', metric.value))
  onFCP((metric: Metric) => reportWebVital('FCP', metric.value))
  onINP((metric: Metric) => reportWebVital('INP', metric.value))
  onCLS((metric: Metric) => reportWebVital('CLS', metric.value))
  onTTFB((metric: Metric) => reportWebVital('TTFB', metric.value))
}
