/*global window, rJS, RSVP, Handlebars, SimpleQuery, ComplexQuery, Query */
/*jslint nomen: true, indent: 2, maxerr: 3 */
(function (window, rJS, RSVP, Handlebars, SimpleQuery, ComplexQuery, Query) {
  "use strict";

  /////////////////////////////////////////////////////////////////
  // Handlebars
  /////////////////////////////////////////////////////////////////
  // Precompile the templates while loading the first gadget instance
  var gadget_klass = rJS(window),
    source = gadget_klass.__template_element
                         .getElementById("table-template")
                         .innerHTML,
    table_template = Handlebars.compile(source);

  gadget_klass
    /////////////////////////////////////////////////////////////////
    // Acquired methods
    /////////////////////////////////////////////////////////////////
    .declareAcquiredMethod("jio_allDocs", "jio_allDocs")
    .declareAcquiredMethod("updateHeader", "updateHeader")
    .declareAcquiredMethod("getUrlForList", "getUrlForList")

    /////////////////////////////////////////////////////////////////
    // declared methods
    /////////////////////////////////////////////////////////////////
    .declareMethod("render", function (options) {
      var gadget = this,
        id_list = options.id_list || [],
        j,
        query_list = [],
        queue,
        row_list;

      for (j = 0; j < id_list.length; j += 1) {
        query_list.push(new SimpleQuery({
          key: 'relative_url',
          value: id_list[j]
        }));
      }
      if (j === 0) {
        queue = new RSVP.Queue()
          .push(function () {
            return {
              data: {
                rows: []
              }
            };
          });
      } else {
        queue = gadget.jio_allDocs({
          query: Query.objectToSearchText(new ComplexQuery({operator: 'OR', query_list: query_list})),
          select_list: ["title", "translated_portal_type"],
          limit: id_list.length
        });
      }
      return queue
        .push(function (result) {
          var url_for_parameter_list = [
            // Back url
            {command: 'display'},
            // Change language
            {command: 'display', options: {page: 'language'}}
          ],
            i;
          row_list = result.data.rows;
          for (i = 0; i < row_list.length; i += 1) {
            url_for_parameter_list.push({
              command: 'display',
              options: {jio_key: row_list[i].id}
            });
          }
          return gadget.getUrlForList(url_for_parameter_list);
        })
        .push(function (url_list) {
          var i,
            document_list = [],
            document_dict = {};

          for (i = 2; i < url_list.length; i += 1) {
            document_dict[row_list[i - 2].id] = {
              link: url_list[i],
              title: (row_list[i - 2].value.title || row_list[i - 2].id) +
                     " (" + row_list[i - 2].value.translated_portal_type + ")"
            };
          }
          // Sort by access time
          for (i = 0; i < id_list.length; i += 1) {
            if (document_dict.hasOwnProperty(id_list[i])) {
              document_list.push(document_dict[id_list[i]]);
            }
          }
          gadget.element.querySelector('.document_list').innerHTML = table_template(
            {document_list: document_list}
          );
          return gadget.updateHeader({
            page_title: 'History',
            page_icon: 'history',
            front_url: url_list[0],
            language_url: url_list[1]
          });
        });
    })
    .declareMethod("triggerSubmit", function () {
      return;
    });
}(window, rJS, RSVP, Handlebars, SimpleQuery, ComplexQuery, Query));