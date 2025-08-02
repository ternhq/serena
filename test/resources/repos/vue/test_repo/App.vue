<template>
  <div>
    <h1>Vue Test App</h1>
    <p>Is authenticated: {{ isAuthenticated }}</p>
    <button @click="handleLogin">Login</button>
    <button @click="handleLogout">Logout</button>
  </div>
</template>

<script lang="ts">
import { defineComponent } from 'vue';
import { authStore } from './store';

export default defineComponent({
  name: 'App',
  computed: {
    isAuthenticated() {
      return authStore.isAuthenticated;
    }
  },
  methods: {
    handleLogin() {
      authStore.login();
    },
    handleLogout() {
      authStore.logout();
    }
  }
});
</script>

<style scoped>
h1 {
  color: #42b983;
}
</style>