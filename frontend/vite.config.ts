import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({plugins:[react()],build:{rollupOptions:{input:{web:'index.html',mobile:'mobile/index.html'}}},server:{proxy:{'/api':{target:'http://127.0.0.1:8000',changeOrigin:true},'/downloads':{target:'http://127.0.0.1:8000',changeOrigin:true}}}});
