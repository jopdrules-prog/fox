# FOX Producer quantity regression tests

Use Node.js 24.15 or a later Node.js 24 release:

```sh
npm ci
npm test
python3 -m unittest discover -s android/scripts/tests -v
```

The JavaScript suite executes each platform's actual HTML and complete `app.js`
in jsdom with isolated localStorage. No real customer data or network resources
are used. It exercises the navigation and inline quantity change handler, then
creates a fresh document from the saved storage to simulate reloading.

Coverage for both PWA and Android assets:

- Blank, zero and negative quantities become 1; positive integer quantities stay intact.
- Quantity input, budget total/balance and sourcing cost/sales summary agree immediately and after reload.
- Existing invalid, missing, null and string quantities use the same read rule without rewriting storage.
- Editing changes only the target quantity. Other products, photos, metadata, statuses,
  budget, memos and other localStorage entries are preserved.
- Android's product-add form applies the same minimum quantity.

The app treats quantities as whole items and retains integer parsing. This fix
does not migrate or reset existing data. Legacy invalid values are normalized
for display and calculations; editing a quantity persists its normalized value.

These are DOM integration tests, not native Android device, APK installation,
service-worker cache or visual rendering tests.
