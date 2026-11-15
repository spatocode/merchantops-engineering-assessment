<script setup>
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { products } from '../api'
import { catalogueQuery } from '../router'
import { pageNumber, useResource } from '../useResource'
import PaginationControls from '../components/PaginationControls.vue'
import RequestState from '../components/RequestState.vue'

const route = useRoute()
const router = useRouter()
const id = route.params.id
const page = computed(() => pageNumber(route.query.page))
const { data: product, load: loadProduct } = useResource()
const { data, loading, error, load } = useResource()
const timestamp = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })
const fieldNames = { sku: 'SKU', name: 'Product name', category_id: 'Category ID', image_url: 'Image path' }
const actionLabels = {
  'product.updated': 'Product updated',
  'inventory.adjusted': 'Inventory adjusted',
}

function fetchActivity() {
  load((signal) => products.activity(id, page.value, signal))
}

function changePage(number) {
  router.push({ name: 'activity', params: { id }, query: number > 1 ? { page: number } : {} })
}

function valueText(value) {
  if (value === '' || value == null) return 'Empty'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function actionLabel(action) {
  return actionLabels[action] || action
}

function signedDelta(delta) {
  return delta > 0 ? `+${delta}` : String(delta)
}

loadProduct((signal) => products.detail(id, signal))
watch(page, fetchActivity, { immediate: true })
</script>

<template>
  <nav class="breadcrumbs" aria-label="Breadcrumb">
    <RouterLink :to="{ name: 'catalogue', query: catalogueQuery }">Products</RouterLink>
    <span aria-hidden="true">/</span>
    <RouterLink :to="{ name: 'editor', params: { id } }">Product details</RouterLink>
    <span aria-hidden="true">/</span>
    <span aria-current="page">Activity</span>
  </nav>

  <header class="page-header">
    <h1>Product activity</h1>
    <p v-if="product">{{ product.name }} <span class="code">· {{ product.sku }}</span></p>
  </header>

  <section class="panel" aria-label="Activity history" :aria-busy="loading">
    <RequestState :loading="loading" :error="error" label="activity" @retry="fetchActivity" />
    <template v-if="data">
      <div v-if="data.results.length" class="table-scroll">
        <table class="data-table activity-table">
          <thead>
            <tr><th scope="col">Action</th><th scope="col">Actor</th><th scope="col">Timestamp</th><th scope="col">Changes</th></tr>
          </thead>
          <tbody>
            <tr v-for="record in data.results" :key="record.id">
              <th scope="row">{{ actionLabel(record.action) }}</th>
              <td>{{ record.actor || 'Unknown' }}</td>
              <td><time :datetime="record.created_at">{{ timestamp.format(new Date(record.created_at)) }}</time></td>
              <td>
                <dl v-if="record.action === 'inventory.adjusted' && record.details" class="change-list">
                  <div>
                    <dt>Variant</dt>
                    <dd><span class="code">{{ record.details.variant_sku }}</span></dd>
                  </div>
                  <div>
                    <dt>Quantity</dt>
                    <dd>
                      <span class="change-before"><span class="sr-only">From </span>{{ record.details.quantity_before }}</span>
                      <span aria-hidden="true"> → </span>
                      <span class="change-after"><span class="sr-only">to </span>{{ record.details.quantity_after }}</span>
                      <span> ({{ signedDelta(record.details.delta) }})</span>
                    </dd>
                  </div>
                  <div>
                    <dt>Reason</dt>
                    <dd>{{ record.details.reason }}</dd>
                  </div>
                </dl>
                <dl v-else-if="record.details?.changes && Object.keys(record.details.changes).length" class="change-list">
                  <div v-for="(change, field) in record.details.changes" :key="field">
                    <dt class="title-case">{{ fieldNames[field] || field.replaceAll('_', ' ') }}</dt>
                    <dd>
                      <span class="change-before"><span class="sr-only">From </span>{{ valueText(change.from) }}</span>
                      <span aria-hidden="true"> → </span>
                      <span class="change-after"><span class="sr-only">to </span>{{ valueText(change.to) }}</span>
                    </dd>
                  </div>
                </dl>
                <span v-else>—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else class="request-state" role="status">
        <strong>No activity yet</strong>
        <p>Saved product changes will appear here.</p>
      </div>
      <PaginationControls v-if="data.count" :page="page" :result="data" label="records" singular="record" @change="changePage" />
    </template>
  </section>
</template>
