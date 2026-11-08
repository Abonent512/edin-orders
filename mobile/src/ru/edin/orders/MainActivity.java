package ru.edin.orders;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.view.View;
import android.webkit.CookieManager;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

/** Standalone Android thin client. Business rules and authorization live in API. */
public final class MainActivity extends Activity {
    private WebView web;
    private String origin;
    private LinearLayout container;
    private TextView connection;
    private int dp(int value) { return (int) (getResources().getDisplayMetrics().density * value); }

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().setStatusBarColor(Color.rgb(23,58,49));
        getWindow().setNavigationBarColor(Color.rgb(246,247,244));
        container = new LinearLayout(this);
        container.setOrientation(LinearLayout.VERTICAL);
        container.setBackgroundColor(Color.rgb(246,247,244));
        setContentView(container);
        String saved = getSharedPreferences("edin", MODE_PRIVATE).getString("server", "");
        if (saved.isEmpty()) settings(); else open(saved);
    }

    private void settings() {
        if (web != null) { web.destroy(); web = null; }
        container.removeAllViews();
        LinearLayout form = new LinearLayout(this);
        form.setOrientation(LinearLayout.VERTICAL);
        form.setPadding(dp(28), dp(70), dp(28), dp(28));
        container.addView(form);
        TextView logo = new TextView(this);
        logo.setText("един."); logo.setTextSize(46); logo.setTextColor(Color.rgb(23,58,49));
        form.addView(logo);
        TextView title = new TextView(this);
        title.setText("Все ваши заказы.\nВсегда под рукой."); title.setTextSize(25);
        title.setTextColor(Color.rgb(40,65,43));title.setPadding(0,dp(30),0,dp(15));form.addView(title);
        TextView description = new TextView(this);
        description.setText("Укажите HTTPS-адрес сервера ЕДИН. Войдите в тот же аккаунт, что и на сайте — заказы будут общими.");
        description.setTextSize(14);description.setTextColor(Color.rgb(120,140,108));
        description.setPadding(0,0,0,dp(25));form.addView(description);
        EditText address = new EditText(this);
        address.setSingleLine(true);address.setInputType(17);address.setHint("https://orders.example.ru");
        address.setText(getSharedPreferences("edin",MODE_PRIVATE).getString("server",""));
        form.addView(address,new LinearLayout.LayoutParams(-1,dp(56)));
        Button connect = new Button(this);connect.setText("Подключиться");
        connect.setTextColor(Color.WHITE);connect.setBackgroundTintList(android.content.res.ColorStateList.valueOf(Color.rgb(32,69,52)));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1,dp(52));params.topMargin=dp(20);form.addView(connect,params);
        connect.setOnClickListener(v -> {
            try {
                Uri uri = Uri.parse(address.getText().toString().trim());
                if (!"https".equals(uri.getScheme()) || uri.getHost()==null || uri.getUserInfo()!=null || uri.getQuery()!=null || uri.getFragment()!=null)
                    throw new IllegalArgumentException();
                String base = "https://" + uri.getEncodedAuthority();
                getSharedPreferences("edin",MODE_PRIVATE).edit().putString("server",base).apply();open(base);
            } catch(Exception e) { address.setError("Нужен адрес HTTPS-сервера, например https://orders.example.ru"); }
        });
        TextView note = new TextView(this);
        note.setText("Данные сохраняются на вашем сервере. Пароль не хранится в настройках приложения. Для смены сервера используйте кнопку в верхней панели.");
        note.setTextSize(11);note.setTextColor(Color.rgb(150,164,138));note.setPadding(0,dp(22),0,0);form.addView(note);
    }

    private void open(String server) {
        origin=server;container.removeAllViews();
        LinearLayout bar=new LinearLayout(this);bar.setPadding(dp(14),0,dp(8),0);bar.setGravity(android.view.Gravity.CENTER_VERTICAL);bar.setBackgroundColor(Color.rgb(23,58,49));
        connection=new TextView(this);connection.setText(Uri.parse(server).getHost());connection.setTextSize(11);connection.setTextColor(Color.rgb(199,225,171));bar.addView(connection,new LinearLayout.LayoutParams(0,dp(42),1));connection.setGravity(android.view.Gravity.CENTER_VERTICAL);
        Button settings=new Button(this);settings.setText("Сервер");settings.setTextSize(10);settings.setTextColor(Color.WHITE);settings.setBackgroundColor(Color.TRANSPARENT);bar.addView(settings,new LinearLayout.LayoutParams(dp(84),dp(42)));settings.setOnClickListener(v->new AlertDialog.Builder(this).setTitle("Сменить сервер?").setMessage("Приложение закроет текущую страницу. Аккаунт и заказы сохранятся на сервере.").setPositiveButton("Настроить",(d,w)->settings()).setNegativeButton("Назад",null).show());
        container.addView(bar);
        web=new WebView(this);container.addView(web,new LinearLayout.LayoutParams(-1,0,1));
        WebSettings s=web.getSettings();s.setJavaScriptEnabled(true);s.setDomStorageEnabled(true);
        s.setAllowFileAccess(false);s.setAllowContentAccess(false);s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        s.setJavaScriptCanOpenWindowsAutomatically(false);s.setSupportMultipleWindows(false);
        CookieManager.getInstance().setAcceptCookie(true);CookieManager.getInstance().setAcceptThirdPartyCookies(web,false);
        // No JS bridge, no TLS-error overrides, no token in URL or native preferences.
        web.setWebViewClient(new WebViewClient(){
            @Override public boolean shouldOverrideUrlLoading(WebView view,WebResourceRequest request){
                Uri uri=request.getUrl();
                String target=uri.getScheme()+"://"+uri.getEncodedAuthority();
                if(origin.equals(target))return false;
                if("https".equals(uri.getScheme()))try{startActivity(new Intent(Intent.ACTION_VIEW,uri));}catch(Exception ignored){}
                return true;
            }
            @Override public void onPageFinished(WebView view,String url){connection.setText(Uri.parse(origin).getHost());CookieManager.getInstance().flush();}
            @Override public void onReceivedError(WebView view,WebResourceRequest request,WebResourceError error){
                if(request.isForMainFrame())new AlertDialog.Builder(MainActivity.this).setTitle("Нет соединения").setMessage("Проверьте интернет и адрес сервера. Корзина сохранена на устройстве.").setPositiveButton("Повторить",(d,w)->web.loadUrl(origin+"/mobile/")).setNegativeButton("Сервер",(d,w)->settings()).show();
            }
        });
        web.loadUrl(origin+"/mobile/");
    }
    @Override public void onBackPressed(){if(web!=null&&web.canGoBack())web.goBack();else super.onBackPressed();}
    @Override protected void onPause(){super.onPause();CookieManager.getInstance().flush();}
    @Override protected void onDestroy(){if(web!=null)web.destroy();super.onDestroy();}
}
