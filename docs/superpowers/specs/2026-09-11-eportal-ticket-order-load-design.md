# ePortal Ticket Order Loading Design

## Goal

Allow the T2 page to load a complete ePortal order by the ePortal primary-key parameter `id` and display ePortal-compatible values, product rows, attachments, and supported dropdown choices.

## Confirmed ePortal Read Contract

T receives the ePortal order primary key in the T2 URL as `id`. For example, ePortal ID `679` opens `/t2?id=679`.

The T backend, never the browser, retrieves the source order from:

`GET http://10.106.4.173/ae.php/api/ticket?id={id}`

The observed response is a flat JSON object. It contains the ePortal primary key `id`, header fields such as `tax_structure` and `customer_payment_term`, `products`, attachment fields `att1` through `att4`, totals, status, and ePortal metadata. It does not contain a complete field schema or dropdown options.

## Architecture

The browser calls a new same-origin T endpoint with the ePortal `id`. The endpoint calls the ePortal ticket endpoint with a bounded timeout, validates that the returned `id` matches the requested ID, and normalizes the flat ePortal response into the existing T2 order presentation model. It returns no ePortal credentials to the browser.

The T2 page uses this remote order model whenever its URL has `id`. Its current local-order and `intellisight_id` bootstrap paths remain available for compatibility. The remote read path is read-only until ePortal provides and confirms a versioned write API.

## Dropdowns and Controls

T owns a small explicit catalog of options extracted from the supplied ePortal source snapshot:

- Header controls: `buyer_1` (Requester), `tax_structure`, `customer_payment_term`, and `location` (签约公司).
- Product controls: `node_id`, `biz_category`, `currency`, `price`, `tax_pyable`, and `dropship`.

`node_id` options depend on `biz_category`. The catalog will preserve the options currently defined by ePortal source. T2 renders a select control only when the catalog has options for a field; all other editable values remain text or date editors. T2 leaves explicitly computed/readonly fields non-editable.

Customer and product search controls are out of scope for this first read integration because the ticket response does not provide their catalogs and no ePortal server API contract for them has been confirmed.

## Error Handling and Safety

- Reject a missing, non-numeric, or non-positive ePortal `id` before calling ePortal.
- Translate ePortal timeout, non-JSON response, HTTP failure, and returned-ID mismatch into a clear T2 error; do not display raw upstream bodies.
- Do not parse ePortal HTML, use browser tokens, or write ePortal data during this phase.
- The observed ticket endpoint currently succeeds without an authorization header. The client implementation must remain ready to add an ePortal Bearer service token later through server configuration; it must never expose the token to the browser.

## Scope Boundary

This phase solves complete-order read/display and supported dropdown presentation only. It does not claim that changes to a remotely loaded order are persisted in ePortal. Remote save begins only after ePortal supplies a write endpoint, accepted request body, authentication rule, validation behavior, and version-conflict response.

## Tests and Acceptance

- Given `id=679`, the T endpoint requests the configured ePortal ticket URL and returns normalized header fields, products, and attachments.
- Invalid IDs and upstream failures return a controlled error.
- A returned ePortal `id` different from the requested ID is rejected.
- The T2 page requests the T same-origin remote-order endpoint for an `id` URL and renders remote values.
- Header and product controls listed above render their ePortal-derived options; `node_id` changes with the selected category.
- Existing local-order T2 tests remain supported by their legacy URL path.
