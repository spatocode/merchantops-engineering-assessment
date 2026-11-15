<script setup>
import { computed, onScopeDispose, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { products } from '../api'
import { pageNumber, useResource } from '../useResource'
import PaginationControls from '../components/PaginationControls.vue'
import ProductThumbnail from '../components/ProductThumbnail.vue'
import RequestState from '../components/RequestState.vue'
import StatusBadge from '../components/StatusBadge.vue'

const route = useRoute()
const router = useRouter()
const search = computed(() => typeof route.query.search === 'string' ? route.query.search.trim() : '')
const page = computed(() => pageNumber(route.query.page))
const searchInput = ref(search.value)
const { data, loading, error, load } = useResource()
let searchTimer

function fetchPage() {
  load(async (signal) => {
    const catalogue = await products.list(
      search.value,
      page.value,
      signal,
    )

    return {
      ...catalogue,
      results: catalogue.results,
    }
  })
}

watch([search, page], () => {
  clearTimeout(searchTimer)
  searchInput.value = search.value
  fetchPage()
}, { immediate: true })

onScopeDispose(() => clearTimeout(searchTimer))

function catalogueLocation(term, number = 1) {
  const query = {}
  if (term) query.search = term
  if (number > 1) query.page = String(number)
  return { name: 'catalogue', query }
}

function commitSearch() {
  clearTimeout(searchTimer)
  const term = searchInput.value.trim()
  if (term !== search.value) router.replace(catalogueLocation(term))
}

function queueSearch() {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(commitSearch, 300)
}

function clearSearch() {
  searchInput.value = ''
  commitSearch()
}

function changePage(number) {
  if (searchInput.value.trim() !== search.value) {
    commitSearch()
    return
  }
  router.push(catalogueLocation(search.value, number))
}
</script>

<template>
  <header class="page-header">
    <h1>Products</h1>
  </header>

  <section class="panel" aria-label="Product catalogue" :aria-busy="loading">
    <form class="table-toolbar" role="search" @submit.prevent="commitSearch">
      <label class="search-box">
        <span class="search-icon" aria-hidden="true"></span>
        <span class="sr-only">Search by name or SKU</span>
        <input v-model="searchInput" type="search" placeholder="Search by name or SKU" @input="queueSearch" />
      </label>
      <button v-if="searchInput" class="button" type="button" @click="clearSearch">Clear</button>
    </form>

    <RequestState :loading="loading" :error="error" label="products" @retry="fetchPage" />
    <template v-if="data">
      <div v-if="data.results.length" class="table-scroll">
        <table class="data-table catalogue-table">
          <thead>
            <tr><th scope="col">Product</th><th scope="col">SKU</th><th scope="col">Category</th><th scope="col">Status</th></tr>
          </thead>
          <tbody>
            <tr v-for="product in data.results" :key="product.id">
              <th scope="row">
                <RouterLink class="product-link" :to="{ name: 'editor', params: { id: product.id } }">
                  <ProductThumbnail :src="product.image_url" />
                  {{ product.name }}
                </RouterLink>
              </th>
              <td><span class="code">{{ product.sku }}</span></td>
              <td>{{ product.category }}</td>
              <td><StatusBadge :status="product.status" /></td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else class="request-state" role="status">
        <strong>{{ search ? 'No matching products' : 'No products yet' }}</strong>
        <p v-if="search">Try a different product name or SKU.</p>
      </div>
      <PaginationControls v-if="data.count" :page="page" :result="data" label="products" singular="product" @change="changePage" />
    </template>
  </section>
</template>
