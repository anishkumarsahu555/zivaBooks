/**
 * Created by anish on 7/8/18.
 */
if ('serviceWorker' in navigator){
    try {
        navigator.serviceWorker.register('../static/sw/serviceworker.js').then(function () {
            console.log('serviceworker register');
        });

    }catch (error){
        console.log('serviceworker unregister');
    }
}/**
 * Created by anish on 15/7/19.
 */
