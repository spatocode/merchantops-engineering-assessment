<script setup>
import { computed, inject, nextTick, onMounted, onScopeDispose, ref, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { productImagePattern, products } from '../api'
import { catalogueQuery } from '../router'
import { useResource } from '../useResource'
import ProductThumbnail from '../components/ProductThumbnail.vue'
import RequestState from '../components/RequestState.vue'
import StatusBadge from '../components/StatusBadge.vue'

const registerSignOutGuard = inject('merchantops:registerSignOutGuard')
const route = useRoute()
const router = useRouter()
const id = route.params.id
const tabs = ['general', 'pricing', 'inventory', 'availability']
const fields = ['name', 'sku', 'description', 'status', 'image_url']
const tab = computed(() => tabs.includes(route.query.tab) ? route.query.tab : 'general')
const { data: product, loading, error, load } = useResource()
const draft = ref({})
const original = ref({})
const changed = computed(() => fields.filter((field) => draft.value[field] !== original.value[field]))
const dirty = computed(() => changed.value.length > 0)
const saving = ref(false)
const saveError = ref('')
const saved = ref(false)
const adjustDrafts = ref({})
const adjustingId = ref(null)
const adjustError = ref('')
const adjustSuccess = ref('')
const formatPrice = new Intl.NumberFormat(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const tiers = computed(() => [...new Set([
  'retail',
  'wholesale',
  ...(product.value?.variants.flatMap((variant) => variant.prices.map((price) => price.tier)) || []),
])])

function readEditor() {
  load(async (signal) => {
    const [editor, detail] = await Promise.all([
      products.editor(id, signal),
      products.detail(id, signal),
    ])
    return { ...editor, image_url: detail.image_url }
  })
}

function resetForm(source) {
  const values = Object.fromEntries(fields.map((field) => [field, source[field] ?? '']))
  original.value = values
  draft.value = { ...values }
}

watch(product, (record) => {
  if (!record) return
  resetForm(record)
  const drafts = { ...adjustDrafts.value }
  for (const variant of record.variants) {
    if (!drafts[variant.id]) drafts[variant.id] = { delta: '', reason: '' }
  }
  adjustDrafts.value = drafts
})

function confirmLeave() {
  if (saving.value) return false
  return !dirty.value || window.confirm('Discard unsaved changes to this product?')
}

function warnBeforeUnload(event) {
  event.preventDefault()
  event.returnValue = ''
}

watch(dirty, (hasChanges) => {
  if (hasChanges) window.addEventListener('beforeunload', warnBeforeUnload)
  else window.removeEventListener('beforeunload', warnBeforeUnload)
}, { flush: 'sync' })

onBeforeRouteLeave(confirmLeave)
onBeforeRouteUpdate((to, from) => to.params.id === from.params.id || confirmLeave())

onMounted(() => {
  registerSignOutGuard({
    confirm: confirmLeave,
    allowUnload: () => window.removeEventListener('beforeunload', warnBeforeUnload),
  })
  readEditor()
})

onScopeDispose(() => {
  window.removeEventListener('beforeunload', warnBeforeUnload)
  registerSignOutGuard(null)
})

async function selectTab(name, focus = false) {
  const query = name === 'general' ? {} : { tab: name }
  await router.replace({ name: 'editor', params: { id }, query })
  if (focus) {
    await nextTick()
    document.getElementById('tab-' + name)?.focus()
  }
}

function moveTab(event) {
  const index = tabs.indexOf(tab.value)
  let next
  switch (event.key) {
    case 'ArrowLeft': next = (index + tabs.length - 1) % tabs.length; break
    case 'ArrowRight': next = (index + 1) % tabs.length; break
    case 'Home': next = 0; break
    case 'End': next = tabs.length - 1; break
    default: return
  }
  event.preventDefault()
  selectTab(tabs[next], true)
}

function priceFor(variant, tier) {
  const price = variant.prices.find((item) => item.tier === tier)
  return price ? formatPrice.format(price.amount) : '—'
}

function clearSaveFeedback() {
  saveError.value = ''
  saved.value = false
}

async function save() {
  if (saving.value || !dirty.value) return
  clearSaveFeedback()
  const body = Object.fromEntries(changed.value.map((field) => [field, draft.value[field]]))
  saving.value = true
  try {
    const updated = await products.update(id, body)
    product.value = { ...product.value, ...updated, category: product.value.category }
    saved.value = true
  } catch (failure) {
    saveError.value = failure.message
  } finally {
    saving.value = false
  }
}

function clearAdjustFeedback() {
  adjustError.value = ''
  adjustSuccess.value = ''
}

async function submitAdjustment(variant) {
  if (!variant.inventory || adjustingId.value) return

  const draftValues = adjustDrafts.value[variant.id] || { delta: '', reason: '' }
  const delta = Number(draftValues.delta)
  const reason = draftValues.reason.trim()

  clearAdjustFeedback()

  if (!Number.isInteger(delta) || delta === 0) {
    adjustError.value = 'Enter a nonzero whole-number adjustment.'
    return
  }
  if (!reason) {
    adjustError.value = 'A reason is required for inventory adjustments.'
    return
  }

  adjustingId.value = variant.id
  try {
    const result = await products.adjustInventory(id, variant.id, { delta, reason })
    product.value = {
      ...product.value,
      variants: product.value.variants.map((item) => (
        item.id === variant.id
          ? {
              ...item,
              inventory: {
                ...item.inventory,
                quantity: result.quantity,
                reserved: result.reserved,
              },
            }
          : item
      )),
    }
    adjustDrafts.value[variant.id] = { delta: '', reason: '' }
    adjustSuccess.value = `${variant.sku}: quantity ${result.quantity_before} → ${result.quantity_after}.`
  } catch (failure) {
    adjustError.value = failure.message
  } finally {
    adjustingId.value = null
  }
}
</script>

<template>
  <nav class="breadcrumbs" aria-label="Breadcrumb">
    <RouterLink :to="{ name: 'catalogue', query: catalogueQuery }">Products</RouterLink>
    <span aria-hidden="true">/</span>
    <span aria-current="page">Product details</span>
  </nav>

  <RequestState :loading="loading" :error="error" label="product details" @retry="readEditor" />
  <template v-if="product">
    <header class="page-header page-header-row">
      <div>
        <div class="title-row">
          <h1>{{ product.name }}</h1>
          <StatusBadge :status="product.status" />
        </div>
        <p class="code">{{ product.sku }}</p>
      </div>
      <RouterLink class="button" :to="{ name: 'activity', params: { id } }">View activity <span aria-hidden="true">→</span></RouterLink>
    </header>

    <div class="tabs" role="tablist" aria-label="Product information" @keydown="moveTab">
      <button v-for="name in tabs" :id="'tab-' + name" :key="name" type="button" role="tab"
        :aria-selected="tab === name" :aria-controls="'panel-' + name" :tabindex="tab === name ? 0 : -1"
        @click="selectTab(name)">
        {{ name }}
      </button>
    </div>

    <section v-show="tab === 'general'" id="panel-general" class="panel tab-panel" role="tabpanel" aria-labelledby="tab-general">
      <form @submit.prevent="save" @input="clearSaveFeedback">
        <div class="form-content">
          <h2>Product information</h2>
          <fieldset class="editor-fields" :disabled="saving">
            <div class="form-grid">
              <label class="field">Product name
                <input v-model="draft.name" required maxlength="200" />
              </label>
              <label class="field">SKU
                <input v-model="draft.sku" required maxlength="64" />
              </label>
              <label class="field">Category
                <input :value="product.category.name" readonly aria-describedby="category-help" />
                <small id="category-help">Category changes are unavailable here.</small>
              </label>
              <label class="field">Status
                <select v-model="draft.status">
                  <option value="draft">Draft</option>
                  <option value="active">Active</option>
                  <option value="archived">Archived</option>
                </select>
              </label>
              <label class="field field-wide">Description
                <textarea v-model="draft.description" rows="4"></textarea>
              </label>
            </div>

            <div class="image-field">
              <ProductThumbnail class="editor-thumbnail" :src="draft.image_url" alt="Product preview" />
              <label class="field">Image path
                <input v-model="draft.image_url" :pattern="productImagePattern" maxlength="255" placeholder="/product-images/example.webp" aria-describedby="image-help" />
                <small id="image-help">Use a local WebP path in /product-images/.</small>
              </label>
            </div>
          </fieldset>
        </div>

        <div class="form-footer">
          <div class="save-feedback">
            <p v-if="saveError" class="error-text" role="alert">{{ saveError }}</p>
            <p v-else-if="saved" class="success-text" role="status">Product saved.</p>
            <p v-else-if="dirty" role="status">Unsaved changes</p>
          </div>
          <button class="button button-primary" type="submit" :disabled="!dirty || saving">
            {{ saving ? 'Saving…' : 'Save changes' }}
          </button>
        </div>
      </form>
    </section>

    <section v-show="tab === 'pricing'" id="panel-pricing" class="panel tab-panel" role="tabpanel" aria-labelledby="tab-pricing" tabindex="0">
      <h2 class="panel-heading">Variant pricing</h2>
      <div v-if="product.variants.length" class="table-scroll">
        <table class="data-table">
          <thead>
            <tr><th scope="col">Variant</th><th scope="col">SKU</th><th v-for="tier in tiers" :key="tier" scope="col" class="numeric title-case">{{ tier }}</th></tr>
          </thead>
          <tbody>
            <tr v-for="variant in product.variants" :key="variant.id">
              <th scope="row">{{ variant.name }}</th>
              <td class="code">{{ variant.sku }}</td>
              <td v-for="tier in tiers" :key="tier" class="numeric">{{ priceFor(variant, tier) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="request-state">No variants for this product.</p>
    </section>

    <section v-show="tab === 'inventory'" id="panel-inventory" class="panel tab-panel" role="tabpanel" aria-labelledby="tab-inventory" tabindex="0">
      <h2 class="panel-heading">Variant inventory</h2>
      <div
        v-if="adjustError || adjustSuccess"
        class="feedback-banner"
        :class="adjustError ? 'feedback-banner-error' : 'feedback-banner-success'"
        :role="adjustError ? 'alert' : 'status'"
      >
        <strong>{{ adjustError ? 'Adjustment failed' : 'Adjustment saved' }}</strong>
        <p>{{ adjustError || adjustSuccess }}</p>
      </div>
      <div v-if="product.variants.length" class="table-scroll">
        <table class="data-table inventory-table">
          <thead>
            <tr>
              <th scope="col">Variant</th>
              <th scope="col">SKU</th>
              <th scope="col" class="numeric">Quantity</th>
              <th scope="col" class="numeric">Reserved</th>
              <th scope="col" class="numeric">Available</th>
              <th scope="col">Adjust stock</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="variant in product.variants" :key="variant.id">
              <th scope="row">{{ variant.name }}</th>
              <td class="code">{{ variant.sku }}</td>
              <td class="numeric">{{ variant.inventory?.quantity ?? '—' }}</td>
              <td class="numeric">{{ variant.inventory?.reserved ?? '—' }}</td>
              <td class="numeric">{{ variant.inventory ? variant.inventory.quantity - variant.inventory.reserved : '—' }}</td>
              <td>
                <form
                  v-if="variant.inventory && adjustDrafts[variant.id]"
                  class="inventory-adjust"
                  @submit.prevent="submitAdjustment(variant)"
                  @input="clearAdjustFeedback"
                >
                  <label class="sr-only" :for="'delta-' + variant.id">Adjustment amount for {{ variant.sku }}</label>
                  <input
                    :id="'delta-' + variant.id"
                    v-model="adjustDrafts[variant.id].delta"
                    type="number"
                    step="1"
                    required
                    :disabled="adjustingId === variant.id"
                    placeholder="± qty"
                  />
                  <label class="sr-only" :for="'reason-' + variant.id">Reason for {{ variant.sku }}</label>
                  <input
                    :id="'reason-' + variant.id"
                    v-model="adjustDrafts[variant.id].reason"
                    type="text"
                    maxlength="255"
                    required
                    :disabled="adjustingId === variant.id"
                    placeholder="Reason"
                  />
                  <button
                    class="button button-primary"
                    type="submit"
                    :disabled="adjustingId !== null"
                  >
                    {{ adjustingId === variant.id ? 'Saving…' : 'Apply' }}
                  </button>
                </form>
                <span v-else class="muted">No inventory record</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="request-state">No variants for this product.</p>
    </section>

    <section v-show="tab === 'availability'" id="panel-availability" class="panel tab-panel" role="tabpanel" aria-labelledby="tab-availability" tabindex="0">
      <h2 class="panel-heading">Sales channels</h2>
      <div v-if="product.availability.length" class="table-scroll">
        <table class="data-table">
          <thead><tr><th scope="col">Channel</th><th scope="col">Status</th></tr></thead>
          <tbody>
            <tr v-for="channel in product.availability" :key="channel.id">
              <th scope="row" class="title-case">{{ channel.channel.replaceAll('_', ' ') }}</th>
              <td><StatusBadge :status="channel.enabled ? 'enabled' : 'disabled'" /></td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="request-state">No sales channels for this product.</p>
    </section>
  </template>
</template>
