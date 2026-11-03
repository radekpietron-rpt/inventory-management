<template>
  <div class="restocking">
    <div class="page-header">
      <h2>{{ t('restocking.title') }}</h2>
      <p>{{ t('restocking.description') }}</p>
    </div>

    <div class="card budget-card">
      <div class="budget-header">
        <label for="restock-budget" class="budget-label">{{ t('restocking.budgetLabel') }}</label>
        <span class="budget-value">{{ formatMoney(budget) }}</span>
      </div>
      <input
        id="restock-budget"
        v-model.number="budget"
        type="range"
        min="0"
        max="60000"
        step="500"
        class="budget-slider"
      />
      <p class="budget-hint">{{ t('restocking.budgetHint') }}</p>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else>
      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-label">{{ t('restocking.summary.plannedCost') }}</div>
          <div class="stat-value">{{ formatMoney(totalCost) }}</div>
        </div>
        <div class="stat-card success">
          <div class="stat-label">{{ t('restocking.summary.remaining') }}</div>
          <div class="stat-value">{{ formatMoney(remainingBudget) }}</div>
        </div>
        <div class="stat-card info">
          <div class="stat-label">{{ t('restocking.summary.itemsFunded') }}</div>
          <div class="stat-value">{{ fundedItemCount }} / {{ recommendationCount }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ t('restocking.summary.leadTime') }}</div>
          <div class="stat-value">{{ maxLeadTimeDays !== null ? t('restocking.days', { days: maxLeadTimeDays }) : '-' }}</div>
        </div>
      </div>

      <div v-if="orderSuccess" class="banner success-banner">
        <span>{{ t('restocking.orderSuccess', { orderNumber: orderSuccess.orderNumber, days: orderSuccess.days }) }}</span>
        <router-link to="/orders">{{ t('restocking.viewInOrders') }}</router-link>
      </div>
      <div v-if="orderError" class="banner error-banner">
        {{ t('restocking.orderErrorMessage') }}
      </div>

      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.recommendationsTitle') }}</h3>
          <button
            class="place-order-btn"
            :disabled="fundedItemCount === 0 || placingOrder || loading || submittedForCurrentPlan"
            @click="placeOrder"
          >
            {{ placingOrder ? t('restocking.placingOrder') : t('restocking.placeOrder') }}
          </button>
        </div>

        <div v-if="recommendationCount === 0" class="empty-state">
          {{ t('restocking.noRecommendations') }}
        </div>
        <template v-else>
          <div v-if="fundedItemCount === 0" class="hint-banner">
            {{ t('restocking.increaseBudget') }}
          </div>
          <div class="table-container">
            <table>
              <thead>
                <tr>
                  <th>{{ t('restocking.table.sku') }}</th>
                  <th>{{ t('restocking.table.item') }}</th>
                  <th>{{ t('restocking.table.warehouse') }}</th>
                  <th>{{ t('restocking.table.onHand') }}</th>
                  <th>{{ t('restocking.table.reorderPoint') }}</th>
                  <th>{{ t('restocking.table.targetStock') }}</th>
                  <th>{{ t('restocking.table.forecast') }}</th>
                  <th>{{ t('restocking.table.trend') }}</th>
                  <th>{{ t('restocking.table.orderQty') }}</th>
                  <th>{{ t('restocking.table.unitCost') }}</th>
                  <th>{{ t('restocking.table.cost') }}</th>
                  <th>{{ t('restocking.table.leadTime') }}</th>
                  <th>{{ t('restocking.table.funding') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="item in items"
                  :key="item.sku"
                  :class="{ 'row-unfunded': item.funding_status === 'unfunded' }"
                >
                  <td><strong>{{ item.sku }}</strong></td>
                  <td>{{ translateProductName(item.name) }}</td>
                  <td>{{ translateWarehouse(item.warehouse) }}</td>
                  <td>
                    <span
                      :class="{ 'value-danger': item.below_reorder_point }"
                      :title="item.below_reorder_point ? t('restocking.belowReorderPoint') : null"
                    >
                      {{ item.quantity_on_hand }}
                    </span>
                  </td>
                  <td>{{ item.reorder_point }}</td>
                  <td>{{ item.target_stock }}</td>
                  <td>{{ item.forecasted_demand }}</td>
                  <td>
                    <span :class="['badge', item.trend]">{{ t('trends.' + item.trend) }}</span>
                  </td>
                  <td>
                    <template v-if="item.funding_status === 'unfunded'">
                      <span class="qty-muted">{{ item.recommended_quantity }}</span>
                    </template>
                    <template v-else>
                      <strong>{{ item.funded_quantity }}</strong>
                      <div v-if="item.funding_status === 'partial'" class="sub-line">
                        {{ t('restocking.unitsOfNeeded', { funded: item.funded_quantity, needed: item.recommended_quantity }) }}
                      </div>
                    </template>
                  </td>
                  <td>{{ formatUnitCost(item.unit_cost) }}</td>
                  <td>
                    <span v-if="item.funding_status === 'unfunded'" class="cost-muted">{{ formatMoney(item.full_cost) }}</span>
                    <strong v-else>{{ formatMoney(item.funded_cost) }}</strong>
                  </td>
                  <td>{{ t('restocking.days', { days: item.lead_time_days }) }}</td>
                  <td>
                    <span :class="['badge', 'funding-' + item.funding_status]">
                      {{ t('restocking.funding.' + item.funding_status) }}
                    </span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { api } from '../api'
import { useI18n } from '../composables/useI18n'
import {
  formatCurrency as formatCurrencyUtil,
  formatCurrencyWithDecimals as formatCurrencyWithDecimalsUtil
} from '../utils/currency'

export default {
  name: 'Restocking',
  setup() {
    const { t, currentCurrency, translateProductName, translateWarehouse } = useI18n()

    const budget = ref(20000)
    const loading = ref(true)
    const error = ref(null)

    const items = ref([])
    const totalCost = ref(0)
    const remainingBudget = ref(0)
    const fundedItemCount = ref(0)
    const recommendationCount = ref(0)

    const placingOrder = ref(false)
    const orderSuccess = ref(null)
    const orderError = ref(false)
    // Guards against re-submitting the same funded plan after a successful order;
    // cleared whenever the budget changes or a fresh set of recommendations loads.
    const submittedForCurrentPlan = ref(false)

    const formatMoney = (value) => formatCurrencyUtil(value, currentCurrency.value)
    const formatUnitCost = (value) => formatCurrencyWithDecimalsUtil(value, currentCurrency.value, 2)

    // Monotonic token guarding against out-of-order responses: the debounced watch
    // below and the initial onMounted call can overlap, and responses may resolve
    // out of order. Only the response matching the latest request is applied.
    let requestSeq = 0

    const loadRecommendations = async () => {
      const seq = ++requestSeq
      loading.value = true
      error.value = null
      orderSuccess.value = null
      orderError.value = false
      submittedForCurrentPlan.value = false
      try {
        const res = await api.getRestockRecommendations(budget.value)
        if (seq !== requestSeq) return // superseded by a newer request
        totalCost.value = res.total_cost
        remainingBudget.value = res.remaining_budget
        fundedItemCount.value = res.funded_item_count
        recommendationCount.value = res.recommendation_count
        items.value = res.items
      } catch (err) {
        if (seq !== requestSeq) return // superseded by a newer request
        error.value = t('restocking.loadError')
        console.error('Failed to load restock recommendations:', err)
      } finally {
        if (seq === requestSeq) loading.value = false
      }
    }

    // Debounce API calls triggered by slider movement while keeping the
    // displayed budget value (v-model) instantly reactive.
    let debounceTimer = null
    watch(budget, () => {
      // A changed budget invalidates the current funded plan immediately,
      // even before the debounced reload below completes.
      submittedForCurrentPlan.value = false
      if (debounceTimer) clearTimeout(debounceTimer)
      debounceTimer = setTimeout(() => {
        loadRecommendations()
      }, 300)
    })

    onUnmounted(() => {
      if (debounceTimer) clearTimeout(debounceTimer)
    })

    const placeOrder = async () => {
      if (fundedItemCount.value === 0 || placingOrder.value) return

      placingOrder.value = true
      orderError.value = false
      orderSuccess.value = null

      try {
        const payload = items.value
          .filter(item => item.funded_quantity > 0)
          .map(item => ({ sku: item.sku, quantity: item.funded_quantity }))

        const order = await api.submitRestockOrder(payload)

        orderSuccess.value = {
          orderNumber: order.order_number,
          days: order.lead_time_days
        }
        // Prevent re-submitting the same (now-placed) plan until the budget
        // changes or a fresh set of recommendations loads.
        submittedForCurrentPlan.value = true
      } catch (err) {
        orderError.value = true
        console.error('Failed to submit restock order:', err)
      } finally {
        placingOrder.value = false
      }
    }

    // Max lead time across funded items only, for the "Max Lead Time" summary tile.
    const maxLeadTimeDays = computed(() => {
      const funded = items.value.filter(item => item.funded_quantity > 0)
      if (funded.length === 0) return null
      return Math.max(...funded.map(item => item.lead_time_days))
    })

    onMounted(loadRecommendations)

    return {
      t,
      budget,
      loading,
      error,
      items,
      totalCost,
      remainingBudget,
      fundedItemCount,
      recommendationCount,
      maxLeadTimeDays,
      placingOrder,
      orderSuccess,
      orderError,
      submittedForCurrentPlan,
      formatMoney,
      formatUnitCost,
      placeOrder,
      translateProductName,
      translateWarehouse
    }
  }
}
</script>

<style scoped>
.budget-card {
  margin-bottom: 1.5rem;
}

.budget-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 0.75rem;
}

.budget-label {
  font-size: 0.875rem;
  font-weight: 600;
  color: #64748b;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.budget-value {
  font-size: 1.75rem;
  font-weight: 700;
  color: #0f172a;
  letter-spacing: -0.025em;
}

.budget-slider {
  width: 100%;
  -webkit-appearance: none;
  appearance: none;
  height: 6px;
  border-radius: 3px;
  background: #e2e8f0;
  outline: none;
  margin: 0.5rem 0;
  cursor: pointer;
}

.budget-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #2563eb;
  border: 3px solid white;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.25);
  cursor: pointer;
  transition: transform 0.15s ease;
}

.budget-slider::-webkit-slider-thumb:hover {
  transform: scale(1.1);
}

.budget-slider::-moz-range-track {
  height: 6px;
  border-radius: 3px;
  background: #e2e8f0;
}

.budget-slider::-moz-range-thumb {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #2563eb;
  border: 3px solid white;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.25);
  cursor: pointer;
}

.budget-hint {
  font-size: 0.813rem;
  color: #64748b;
  margin-top: 0.5rem;
}

.banner {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
  padding: 0.875rem 1.25rem;
  border-radius: 8px;
  margin-bottom: 1.25rem;
  font-size: 0.938rem;
}

.success-banner {
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  color: #166534;
}

.success-banner a {
  color: #166534;
  font-weight: 700;
  text-decoration: underline;
}

.error-banner {
  background: #fef2f2;
  border: 1px solid #fecaca;
  color: #991b1b;
}

.hint-banner {
  padding: 0.875rem 1.5rem;
  background: #fffbeb;
  border-bottom: 1px solid #fde68a;
  color: #92400e;
  font-size: 0.875rem;
}

.empty-state {
  padding: 3rem;
  text-align: center;
  color: #64748b;
  font-size: 0.938rem;
}

.place-order-btn {
  padding: 0.625rem 1.5rem;
  background: #2563eb;
  color: white;
  border: none;
  border-radius: 8px;
  font-size: 0.875rem;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s ease;
  white-space: nowrap;
}

.place-order-btn:hover:not(:disabled) {
  background: #1d4ed8;
}

.place-order-btn:disabled {
  background: #cbd5e1;
  cursor: not-allowed;
}

.value-danger {
  color: #dc2626;
  font-weight: 700;
}

.qty-muted,
.cost-muted {
  color: #94a3b8;
}

.sub-line {
  font-size: 0.75rem;
  color: #64748b;
  margin-top: 0.125rem;
}

.row-unfunded {
  opacity: 0.55;
}

.badge.funding-full {
  background: #d1fae5;
  color: #065f46;
}

.badge.funding-partial {
  background: #fef3c7;
  color: #92400e;
}

.badge.funding-unfunded {
  background: #e2e8f0;
  color: #64748b;
}
</style>
